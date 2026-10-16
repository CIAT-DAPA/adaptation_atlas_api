"""Tabular reader that queries Hive-partitioned Parquet files with DuckDB.

The reader turns a :class:`TabularQuery` into a parameterized SQL statement:

* the storage path is built from the dataset ``path_template``, replacing each
  partition placeholder with the requested value or ``*`` when not filtered;
* filters on columns are bound as query parameters (never interpolated);
* ``hive_partitioning`` exposes the partition values as columns and
  ``union_by_name`` tolerates schema differences between files;
* response fields missing from every matched file are returned as ``NULL``.
"""

from __future__ import annotations

import logging
import math
import threading
from typing import Any

import duckdb

from adaptation_atlas_api.domain.exceptions import QueryExecutionError, SourceUnavailableError
from adaptation_atlas_api.domain.models.dataset import DatasetDefinition, FieldType
from adaptation_atlas_api.domain.models.query import Predicate, PredicateOperator, TabularQuery
from adaptation_atlas_api.domain.models.results import TabularResult
from adaptation_atlas_api.ports.storage import Storage
from adaptation_atlas_api.ports.tabular_reader import TabularReader

logger = logging.getLogger(__name__)

TOTAL_COLUMN = "__total_count"

_CAST_BY_TYPE = {
    FieldType.STRING: "VARCHAR",
    FieldType.NUMBER: "DOUBLE",
    FieldType.INTEGER: "BIGINT",
}


def _quote_identifier(name: str) -> str:
    """Quote a SQL identifier, escaping embedded double quotes.

    Args:
        name: Raw identifier.

    Returns:
        The identifier wrapped in double quotes.
    """
    return '"' + name.replace('"', '""') + '"'


class DuckDBTabularReader(TabularReader):
    """Executes tabular queries over Parquet files using an embedded DuckDB engine."""

    def __init__(
        self,
        storage: Storage,
        *,
        memory_limit: str = "2GB",
        threads: int = 4,
        query_timeout_seconds: int = 60,
    ) -> None:
        """Create the reader.

        Args:
            storage: Storage adapter resolving URIs and configuring credentials.
            memory_limit: Memory limit handed to DuckDB (e.g. ``2GB``).
            threads: Number of threads DuckDB may use.
            query_timeout_seconds: Seconds after which a running query is interrupted.
        """
        self._storage = storage
        self._memory_limit = memory_limit
        self._threads = threads
        self._timeout = query_timeout_seconds
        self._connection: duckdb.DuckDBPyConnection | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def query(self, query: TabularQuery) -> TabularResult:
        """Execute ``query`` and return the matching records.

        Args:
            query: A validated tabular query.

        Returns:
            The matching records, or an empty result when no file or row matches.

        Raises:
            SourceUnavailableError: When the storage cannot be read.
            QueryExecutionError: When DuckDB fails or the query times out.
        """
        dataset = query.dataset
        columns = [item.name for item in dataset.response_fields]
        relative_path = self.build_relative_path(query)
        uri = self._storage.resolve_uri(relative_path)
        cursor = self._get_connection().cursor()

        try:
            source_files = self._count_files(cursor, uri)
            # A path that matches no file is a valid request with no data.
            if source_files == 0:
                logger.info(
                    "No source files for dataset '%s' with path '%s'", dataset.id, relative_path
                )
                return TabularResult.empty(
                    dataset.id, columns, query.limit, query.offset, query.filters.applied
                )

            available_columns = self._describe_columns(cursor, uri)
            sql, parameters = self._build_sql(query, uri, available_columns)
            rows = self._execute_with_timeout(cursor, sql, parameters)
        except (duckdb.IOException, duckdb.InvalidInputException) as error:
            # Includes HTTP/S3 failures and corrupt files: report a controlled error
            # without exposing the URI.
            logger.error("Storage error reading dataset '%s': %s", dataset.id, error)
            raise SourceUnavailableError(
                f"The source data for dataset '{dataset.id}' is currently unavailable."
            ) from error
        except duckdb.InterruptException as error:
            logger.error("Query timeout for dataset '%s'", dataset.id)
            raise QueryExecutionError(
                f"The query for dataset '{dataset.id}' exceeded the time limit."
            ) from error
        except duckdb.Error as error:
            logger.error("DuckDB error for dataset '%s': %s", dataset.id, error)
            raise QueryExecutionError(
                f"The query for dataset '{dataset.id}' could not be executed."
            ) from error
        finally:
            cursor.close()

        total_count = int(rows[0][-1]) if rows else 0
        # When the page is empty but the offset is positive the total is still unknown.
        if not rows and query.offset > 0:
            total_count = self._count_rows(query, uri, available_columns)

        records = [self._row_to_record(columns, row[:-1]) for row in rows]
        return TabularResult(
            dataset_id=dataset.id,
            columns=columns,
            records=records,
            total_count=total_count,
            limit=query.limit,
            offset=query.offset,
            applied_filters=dict(query.filters.applied),
            source_files=source_files,
        )

    @staticmethod
    def build_relative_path(query: TabularQuery) -> str:
        """Render the dataset path template for the requested partitions and files.

        Args:
            query: The validated query.

        Returns:
            A path (or glob) relative to the storage root.
        """
        dataset = query.dataset
        storage = dataset.storage
        path = storage.path_template
        file_glob = storage.file_patterns[query.filters.file_pattern_key]

        # Replace every placeholder with the requested value, or a wildcard when absent.
        for placeholder in dataset.template_placeholders():
            # The file placeholder is replaced by the glob of the selected file pattern.
            if placeholder == "file":
                replacement = file_glob
            else:
                replacement = query.filters.partition_values.get(placeholder, "*")
            path = path.replace("{" + placeholder + "}", replacement)
        return path

    # ------------------------------------------------------------------ #
    # SQL construction
    # ------------------------------------------------------------------ #
    def _build_sql(
        self,
        query: TabularQuery,
        uri: str,
        available_columns: set[str],
        *,
        count_only: bool = False,
    ) -> tuple[str, list[Any]]:
        """Build the SQL statement and its bound parameters for ``query``.

        Args:
            query: The validated query.
            uri: Resolved storage URI (possibly a glob).
            available_columns: Columns present in at least one matched file.
            count_only: When ``True`` build a ``COUNT(*)`` statement instead of a page.

        Returns:
            The SQL text and the ordered list of parameters to bind.
        """
        dataset = query.dataset
        parameters: list[Any] = [uri]
        source = "read_parquet(?, hive_partitioning=true, union_by_name=true)"
        where_sql, where_parameters = self._build_where(query, available_columns)
        parameters.extend(where_parameters)

        # Count statements only need the source and the filters.
        if count_only:
            return f"SELECT COUNT(*) FROM {source}{where_sql}", parameters

        select_sql = self._build_select(dataset, available_columns)
        order_sql = self._build_order_by(dataset)
        parameters.extend([query.limit, query.offset])
        sql = (
            f"SELECT {select_sql}, COUNT(*) OVER () AS {TOTAL_COLUMN} "
            f"FROM {source}{where_sql}{order_sql} LIMIT ? OFFSET ?"
        )
        return sql, parameters

    @staticmethod
    def _build_select(dataset: DatasetDefinition, available_columns: set[str]) -> str:
        """Build the projection list casting each response field to its logical type.

        Args:
            dataset: Dataset describing the response fields.
            available_columns: Columns present in the matched files.

        Returns:
            The comma separated projection expressions.
        """
        expressions: list[str] = []

        # Project every response field, substituting NULL for absent source columns.
        for field in dataset.response_fields:
            alias = _quote_identifier(field.name)
            cast_type = _CAST_BY_TYPE[field.type]
            # Absent columns are still returned so every record has the same shape.
            if field.resolved_source not in available_columns:
                expressions.append(f"CAST(NULL AS {cast_type}) AS {alias}")
            else:
                expressions.append(
                    f"CAST({_quote_identifier(field.resolved_source)} AS {cast_type}) AS {alias}"
                )
        return ", ".join(expressions)

    @staticmethod
    def _build_where(query: TabularQuery, available_columns: set[str]) -> tuple[str, list[Any]]:
        """Build the ``WHERE`` clause from column filters, partitions and derived predicates.

        Args:
            query: The validated query.
            available_columns: Columns present in the matched files.

        Returns:
            The ``WHERE`` clause (with a leading space) and its bound parameters. An empty
            clause is returned when there are no conditions.
        """
        conditions: list[str] = []
        parameters: list[Any] = []

        # Partition values are also enforced on the Hive columns as a safety net.
        for partition, value in sorted(query.filters.partition_values.items()):
            # Only partitions exposed as columns can be used in the WHERE clause.
            if partition in available_columns:
                conditions.append(f"{_quote_identifier(partition)} = ?")
                parameters.append(value)

        # Column filters become bound equality predicates.
        for column, value in sorted(query.filters.column_filters.items()):
            conditions.append(f"{_quote_identifier(column)} = ?")
            parameters.append(value)

        # Derived predicates are translated operator by operator.
        for predicate in query.filters.derived_predicates:
            condition, parameter = DuckDBTabularReader._predicate_to_sql(predicate)
            conditions.append(condition)
            # Null checks carry no parameter.
            if parameter is not None:
                parameters.append(parameter)

        # No conditions means no WHERE clause at all.
        if not conditions:
            return "", parameters
        return " WHERE " + " AND ".join(conditions), parameters

    @staticmethod
    def _predicate_to_sql(predicate: Predicate) -> tuple[str, Any]:
        """Translate a storage-agnostic predicate into SQL.

        Args:
            predicate: The predicate to translate.

        Returns:
            The SQL condition and the parameter to bind (``None`` for null checks).

        Raises:
            QueryExecutionError: If the operator is not supported.
        """
        column = _quote_identifier(predicate.column)

        # Equality binds the value as a parameter.
        if predicate.operator is PredicateOperator.EQUALS:
            return f"{column} = ?", predicate.value
        # Null checks need no parameter.
        if predicate.operator is PredicateOperator.IS_NULL:
            return f"{column} IS NULL", None
        # Not-null checks need no parameter either.
        if predicate.operator is PredicateOperator.IS_NOT_NULL:
            return f"{column} IS NOT NULL", None
        raise QueryExecutionError(f"Unsupported predicate operator '{predicate.operator}'.")

    @staticmethod
    def _build_order_by(dataset: DatasetDefinition) -> str:
        """Build the ``ORDER BY`` clause guaranteeing stable pagination.

        Args:
            dataset: Dataset declaring the sort fields.

        Returns:
            The ``ORDER BY`` clause with a leading space, or an empty string.
        """
        # Without declared sort fields DuckDB returns rows in scan order.
        if not dataset.order_by:
            return ""
        return " ORDER BY " + ", ".join(_quote_identifier(name) for name in dataset.order_by)

    # ------------------------------------------------------------------ #
    # Execution helpers
    # ------------------------------------------------------------------ #
    def _get_connection(self) -> duckdb.DuckDBPyConnection:
        """Create (lazily) the shared DuckDB connection with its settings.

        Returns:
            The configured connection.

        Raises:
            SourceUnavailableError: If the storage extension cannot be configured.
        """
        with self._lock:
            # The connection is created once and reused through per-query cursors.
            if self._connection is None:
                connection = duckdb.connect(database=":memory:")
                connection.execute(f"SET memory_limit='{self._memory_limit}';")
                connection.execute(f"SET threads={int(self._threads)};")
                try:
                    self._storage.configure_connection(connection)
                except duckdb.Error as error:
                    logger.error("Could not configure storage access: %s", error)
                    raise SourceUnavailableError(
                        "The storage backend could not be configured."
                    ) from error
                self._connection = connection
            return self._connection

    def _execute_with_timeout(
        self, cursor: duckdb.DuckDBPyConnection, sql: str, parameters: list[Any]
    ) -> list[tuple[Any, ...]]:
        """Run a statement, interrupting it when the configured timeout elapses.

        Args:
            cursor: Cursor to execute on.
            sql: SQL text with ``?`` placeholders.
            parameters: Values bound to the placeholders.

        Returns:
            All result rows.
        """
        timer = threading.Timer(self._timeout, cursor.interrupt)
        timer.daemon = True
        timer.start()
        try:
            logger.debug("DuckDB SQL: %s | params=%s", sql, parameters)
            return cursor.execute(sql, parameters).fetchall()
        finally:
            timer.cancel()

    @staticmethod
    def _count_files(cursor: duckdb.DuckDBPyConnection, uri: str) -> int:
        """Count the files matching ``uri`` without reading their content.

        Args:
            cursor: Cursor to execute on.
            uri: Resolved URI or glob.

        Returns:
            Number of matching files.
        """
        try:
            row = cursor.execute("SELECT COUNT(*) FROM glob(?)", [uri]).fetchone()
        except duckdb.IOException as error:
            # DuckDB reports an empty glob as an IO error on some backends.
            if "No files found" in str(error):
                return 0
            raise
        return int(row[0]) if row else 0

    @staticmethod
    def _describe_columns(cursor: duckdb.DuckDBPyConnection, uri: str) -> set[str]:
        """Return the union of column names across the matched files.

        Args:
            cursor: Cursor to execute on.
            uri: Resolved URI or glob.

        Returns:
            Column names available to the query.
        """
        rows = cursor.execute(
            "DESCRIBE SELECT * FROM read_parquet(?, hive_partitioning=true, union_by_name=true)",
            [uri],
        ).fetchall()
        return {row[0] for row in rows}

    def _count_rows(self, query: TabularQuery, uri: str, available_columns: set[str]) -> int:
        """Count the rows matching ``query`` (used when a page beyond the end is requested).

        Args:
            query: The validated query.
            uri: Resolved URI or glob.
            available_columns: Columns present in the matched files.

        Returns:
            Total number of matching rows.
        """
        cursor = self._get_connection().cursor()
        try:
            sql, parameters = self._build_sql(query, uri, available_columns, count_only=True)
            rows = self._execute_with_timeout(cursor, sql, parameters)
        except duckdb.Error as error:
            logger.error("DuckDB count error for dataset '%s': %s", query.dataset.id, error)
            raise QueryExecutionError(
                f"The query for dataset '{query.dataset.id}' could not be executed."
            ) from error
        finally:
            cursor.close()
        return int(rows[0][0]) if rows else 0

    @staticmethod
    def _row_to_record(columns: list[str], row: tuple[Any, ...]) -> dict[str, Any]:
        """Convert a DuckDB row into a JSON friendly dictionary.

        Args:
            columns: Public field names in projection order.
            row: Values returned by DuckDB.

        Returns:
            The record, with NaN values converted to ``None``.
        """
        record: dict[str, Any] = {}

        # Pair each value with its column name, sanitizing floats JSON cannot encode.
        for name, value in zip(columns, row, strict=True):
            # NaN and infinities are not valid JSON numbers.
            if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
                value = None
            record[name] = value
        return record
