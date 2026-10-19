"""Tabular reader that queries Hive-partitioned Parquet files with DuckDB.

The reader turns a :class:`TabularQuery` into a parameterized SQL statement:

* the storage paths are built from the dataset ``path_template``, replacing each
  partition placeholder with every requested value (or ``*`` when not filtered), and
  expanded to the concrete files that exist;
* filters on columns are bound as query parameters (never interpolated) and several
  values of the same filter become an ``IN`` list;
* ``hive_partitioning`` exposes the partition values as columns and
  ``union_by_name`` tolerates schema differences between files;
* response fields missing from every matched file are returned as ``NULL``;
* datasets without explicit response fields return every source column as is;
* corrupt or truncated files (e.g. an incomplete S3 upload without the Parquet footer)
  are skipped, up to a configurable maximum, and reported in the result instead of
  failing the whole query.
"""

from __future__ import annotations

import datetime as dt
import itertools
import logging
import math
import threading
from decimal import Decimal
from typing import Any

import duckdb

from adaptation_atlas_api.domain.exceptions import (
    FilterNotApplicableError,
    QueryExecutionError,
    SourceUnavailableError,
)
from adaptation_atlas_api.domain.models.dataset import (
    DatasetDefinition,
    FieldType,
    extract_placeholders,
)
from adaptation_atlas_api.domain.models.query import (
    DerivedCondition,
    Predicate,
    PredicateOperator,
    TabularQuery,
)
from adaptation_atlas_api.domain.models.results import TabularResult
from adaptation_atlas_api.ports.storage import Storage
from adaptation_atlas_api.ports.tabular_reader import TabularReader

logger = logging.getLogger(__name__)

TOTAL_COLUMN = "__total_count"

# Fragments (lower case) of DuckDB messages that identify a corrupt or truncated file,
# as opposed to a network, permission or missing-object failure.
_UNREADABLE_MARKERS = (
    "magic bytes",
    "not a parquet file",
    "corrupt",
    "footer",
    "invalid input error",
)

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


_NUMERIC_PREFIXES = ("TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UTINYINT", "USMALLINT")
_CASTABLE_PREFIXES = _NUMERIC_PREFIXES + (
    "UINTEGER",
    "UBIGINT",
    "FLOAT",
    "DOUBLE",
    "DECIMAL",
    "DATE",
    "TIMESTAMP",
    "BOOLEAN",
)


def _typed_placeholder(column_type: str) -> str:
    """Build a bound-parameter placeholder cast to the column type when needed.

    Filter values always arrive as text; comparing them with numeric, date or boolean
    columns requires an explicit cast so DuckDB does not compare as strings.

    Args:
        column_type: DuckDB type name of the column.

    Returns:
        ``?`` for text columns, or ``CAST(? AS <type>)`` for castable types.
    """
    upper = column_type.upper()
    # Only well-known scalar types are cast; everything else is compared as text.
    if upper.startswith(_CASTABLE_PREFIXES):
        return f"CAST(? AS {upper})"
    return "?"


def _membership_sql(column: str, column_type: str, count: int) -> str:
    """Build ``column = ?`` or ``column IN (?, ?, ...)`` with typed placeholders.

    Args:
        column: Column name.
        column_type: DuckDB type of the column.
        count: Number of values to bind.

    Returns:
        The SQL condition with ``count`` placeholders.
    """
    placeholder = _typed_placeholder(column_type)
    identifier = _quote_identifier(column)

    # A single value keeps the simpler equality form.
    if count == 1:
        return f"{identifier} = {placeholder}"
    return f"{identifier} IN ({', '.join([placeholder] * count)})"


class DuckDBTabularReader(TabularReader):
    """Executes tabular queries over Parquet files using an embedded DuckDB engine."""

    def __init__(
        self,
        storage: Storage,
        *,
        memory_limit: str = "2GB",
        threads: int = 4,
        query_timeout_seconds: int = 60,
        max_skipped_files: int = 5,
    ) -> None:
        """Create the reader.

        Args:
            storage: Storage adapter resolving URIs and configuring credentials.
            memory_limit: Memory limit handed to DuckDB (e.g. ``2GB``).
            threads: Number of threads DuckDB may use.
            query_timeout_seconds: Seconds after which a running query is interrupted.
            max_skipped_files: Maximum number of unreadable files one query may skip
                before failing; ``0`` means any unreadable file fails the query.
        """
        self._storage = storage
        self._memory_limit = memory_limit
        self._threads = threads
        self._timeout = query_timeout_seconds
        self._max_skipped_files = max_skipped_files
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
            The matching records, or an empty result when no file or row matches. When
            some matched files were unreadable and skipped, they are listed in
            ``skipped_files``.

        Raises:
            SourceUnavailableError: When the storage cannot be read, or more files are
                unreadable than the reader is allowed to skip.
            QueryExecutionError: When DuckDB fails or the query times out.
        """
        dataset = query.dataset
        columns = [item.name for item in dataset.response_fields]
        relative_paths = self.build_relative_paths(query)
        uris = [self._storage.resolve_uri(path) for path in relative_paths]
        cursor = self._get_connection().cursor()
        skipped: list[str] = []

        try:
            files = self._expand_files(cursor, uris)
            # Paths that match no file are a valid request with no data.
            if not files:
                logger.info(
                    "No source files for dataset '%s' with paths %s", dataset.id, relative_paths
                )
                return TabularResult.empty(
                    dataset.id, columns, query.limit, query.offset, query.filters.applied
                )

            # Each attempt reads the current file list; an unreadable file is dropped
            # and the attempt repeated, until the query succeeds or the skip budget
            # is exhausted.
            while True:
                try:
                    column_types = self._describe_columns(cursor, files)
                    available_columns = list(column_types)
                    # Datasets without explicit fields expose the file columns plus
                    # declared partitions, skipping Hive keys the catalog does not declare.
                    if not dataset.has_explicit_fields:
                        columns = self._automatic_columns(cursor, files, dataset, available_columns)
                    sql, parameters = self._build_sql(query, files, column_types, columns=columns)
                    rows = self._execute_with_timeout(cursor, sql, parameters)
                    break
                except (duckdb.IOException, duckdb.InvalidInputException) as error:
                    unreadable = self._unreadable_file(error, files)
                    # Failures not tied to one corrupt file, or beyond the budget, are
                    # reported as unavailable source data.
                    if unreadable is None or len(skipped) >= self._max_skipped_files:
                        raise
                    files.remove(unreadable)
                    partition_path = self._partition_path(dataset, unreadable)
                    skipped.append(partition_path)
                    logger.warning(
                        "Skipping unreadable file for dataset '%s': %s (%s)",
                        dataset.id,
                        unreadable,
                        str(error).splitlines()[0],
                    )
                    # When every matched file was unreadable there is nothing to serve.
                    if not files:
                        raise SourceUnavailableError(
                            f"The source data for dataset '{dataset.id}' is currently "
                            "unavailable: every matching file is unreadable."
                        ) from error
            source_files = len(files)
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
            total_count = self._count_rows(query, files, column_types)

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
            skipped_files=skipped,
        )

    @staticmethod
    def _unreadable_file(error: duckdb.Error, files: list[str]) -> str | None:
        """Identify the corrupt or truncated file named by a DuckDB error, if any.

        Only errors that describe an unreadable file (missing Parquet footer, invalid
        input...) are considered; network, permission or missing-object failures are
        not, so they still fail the query as a whole.

        Args:
            error: The DuckDB exception raised while reading ``files``.
            files: URIs the failing statement was reading.

        Returns:
            The URI of the unreadable file, or ``None`` when the error does not point at
            exactly one corrupt file.
        """
        message = str(error)
        lowered = message.lower()
        # HTTP failures are transport problems, never a corrupt file.
        if "http" in lowered and "magic bytes" not in lowered:
            return None
        # The message must describe a corrupt file, not any other input problem.
        if not any(marker in lowered for marker in _UNREADABLE_MARKERS):
            return None
        # The file is identified by its URI quoted inside the message.
        for uri in files:
            if uri in message:
                return uri
        return None

    def _partition_path(self, dataset: DatasetDefinition, uri: str) -> str:
        """Render a file URI as a path relative to the dataset root, hiding the bucket.

        Args:
            dataset: Dataset the file belongs to.
            uri: Resolved file URI.

        Returns:
            The partition folders and file name below the dataset's static prefix, or the
            bare file name when the URI does not start with that prefix.
        """
        base = self._storage.resolve_uri(dataset.storage.static_prefix).rstrip("/")
        # Files under the dataset root are reported by their partition path.
        if base and uri.startswith(base):
            return uri[len(base) :].lstrip("/")
        return uri.rsplit("/", 1)[-1]

    @staticmethod
    def build_relative_paths(query: TabularQuery) -> list[str]:
        """Render the dataset path template for every requested partition/file combination.

        Each partition placeholder is replaced by each of its requested values (or ``*``
        when the partition is not filtered) and the ``{file}`` placeholder by the glob of
        each selected file pattern, producing one path per combination.

        Args:
            query: The validated query.

        Returns:
            Distinct paths (or globs) relative to the storage root, in a stable order.
        """
        dataset = query.dataset
        storage = dataset.storage
        filters = query.filters
        placeholders = [name for name in dataset.template_placeholders() if name != "file"]
        file_globs = [storage.file_patterns[key] for key in filters.file_pattern_keys]

        # Placeholders used inside file patterns also need their values.
        for file_glob in file_globs:
            for placeholder in extract_placeholders(file_glob):
                # Keep a stable order without duplicating template placeholders.
                if placeholder not in placeholders:
                    placeholders.append(placeholder)

        # Every partition contributes its requested values, or a single wildcard.
        choices = [filters.partition_values.get(name, ["*"]) for name in placeholders]
        paths: list[str] = []

        # Build one path per file glob and per combination of partition values.
        for file_glob in file_globs:
            for combination in itertools.product(*choices):
                values = dict(zip(placeholders, combination, strict=True))
                rendered_glob = file_glob
                # Substitute the placeholders embedded in the file name first.
                for placeholder, value in values.items():
                    rendered_glob = rendered_glob.replace("{" + placeholder + "}", value)
                path = storage.path_template.replace("{file}", rendered_glob)
                # Then substitute the folder placeholders of the template.
                for placeholder, value in values.items():
                    path = path.replace("{" + placeholder + "}", value)
                # Identical paths can arise when a pattern ignores a placeholder.
                if path not in paths:
                    paths.append(path)
        return paths

    # ------------------------------------------------------------------ #
    # SQL construction
    # ------------------------------------------------------------------ #
    def _build_sql(
        self,
        query: TabularQuery,
        files: list[str],
        column_types: dict[str, str],
        *,
        columns: list[str] | None = None,
        count_only: bool = False,
    ) -> tuple[str, list[Any]]:
        """Build the SQL statement and its bound parameters for ``query``.

        Args:
            query: The validated query.
            files: Resolved URIs of the files to read.
            column_types: Column name -> DuckDB type for every column in the matched files.
            columns: Columns to project for datasets without explicit fields.
            count_only: When ``True`` build a ``COUNT(*)`` statement instead of a page.

        Returns:
            The SQL text and the ordered list of parameters to bind.
        """
        dataset = query.dataset
        parameters: list[Any] = [files]
        source = "read_parquet(?, hive_partitioning=true, union_by_name=true)"
        available_columns = list(column_types)
        where_sql, where_parameters = self._build_where(query, column_types)
        parameters.extend(where_parameters)

        # Count statements only need the source and the filters.
        if count_only:
            return f"SELECT COUNT(*) FROM {source}{where_sql}", parameters

        select_sql = self._build_select(dataset, available_columns, columns)
        order_sql = self._build_order_by(dataset, columns or available_columns)
        parameters.extend([query.limit, query.offset])
        sql = (
            f"SELECT {select_sql}, COUNT(*) OVER () AS {TOTAL_COLUMN} "
            f"FROM {source}{where_sql}{order_sql} LIMIT ? OFFSET ?"
        )
        return sql, parameters

    @staticmethod
    def _build_select(
        dataset: DatasetDefinition,
        available_columns: list[str],
        automatic_columns: list[str] | None = None,
    ) -> str:
        """Build the projection list casting each response field to its logical type.

        Args:
            dataset: Dataset describing the response fields.
            available_columns: Columns present in the matched files, in file order.
            automatic_columns: Columns to project when the dataset has no explicit fields.

        Returns:
            The comma separated projection expressions.
        """
        # Without explicit fields the automatic columns are projected with native types.
        if not dataset.has_explicit_fields:
            projected = automatic_columns or available_columns
            return ", ".join(_quote_identifier(column) for column in projected)

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
    def _build_where(query: TabularQuery, column_types: dict[str, str]) -> tuple[str, list[Any]]:
        """Build the ``WHERE`` clause from column filters, partitions and derived conditions.

        Several values of the same filter are combined with OR (``IN`` lists, or OR-ed
        predicate groups for derived filters); different filters are combined with AND.

        Args:
            query: The validated query.
            column_types: Column name -> DuckDB type for the columns present in the files.

        Returns:
            The ``WHERE`` clause (with a leading space) and its bound parameters. An empty
            clause is returned when there are no conditions.

        Raises:
            FilterNotApplicableError: If a column filter targets a column absent from the
                files selected by the query.
        """
        conditions: list[str] = []
        parameters: list[Any] = []

        # Partition values are also enforced on the Hive columns as a safety net.
        for partition, values in sorted(query.filters.partition_values.items()):
            # Only partitions exposed as columns can be used in the WHERE clause.
            if partition in column_types:
                conditions.append(_membership_sql(partition, column_types[partition], len(values)))
                parameters.extend(values)

        # Column filters become bound membership predicates cast to the column type.
        for column, values in sorted(query.filters.column_filters.items()):
            # A filter on a column absent from the selected files cannot be applied.
            if column not in column_types:
                parameter_name = query.filters.filter_names_by_column.get(column, column)
                raise FilterNotApplicableError(
                    f"Parameter '{parameter_name}' is not applicable to the data selected by "
                    "the other filters.",
                    parameter=parameter_name,
                )
            conditions.append(_membership_sql(column, column_types[column], len(values)))
            parameters.extend(values)

        # Derived conditions are OR-ed alternatives of AND-ed predicates.
        for condition in query.filters.derived_conditions:
            sql, condition_parameters = DuckDBTabularReader._derived_condition_to_sql(
                condition, column_types
            )
            # Conditions that are trivially true are omitted.
            if sql is None:
                continue
            conditions.append(sql)
            parameters.extend(condition_parameters)

        # No conditions means no WHERE clause at all.
        if not conditions:
            return "", parameters
        return " WHERE " + " AND ".join(conditions), parameters

    @staticmethod
    def _derived_condition_to_sql(
        condition: DerivedCondition, column_types: dict[str, str]
    ) -> tuple[str | None, list[Any]]:
        """Translate a derived condition into ``(... AND ...) OR (... AND ...)`` SQL.

        Args:
            condition: The derived condition with its alternatives.
            column_types: Column name -> DuckDB type for the columns present in the files.

        Returns:
            The SQL fragment (``None`` when trivially true) and its bound parameters.
        """
        groups: list[str] = []
        parameters: list[Any] = []

        # Each alternative is a conjunction of predicates.
        for alternative in condition.alternatives:
            pieces: list[str] = []
            # Translate every predicate, dropping the trivially true ones.
            for predicate in alternative:
                sql, parameter = DuckDBTabularReader._predicate_to_sql(
                    predicate, present=predicate.column in column_types
                )
                # ``None`` means the predicate always holds and adds nothing.
                if sql is None:
                    continue
                pieces.append(sql)
                # Null checks carry no parameter.
                if parameter is not None:
                    parameters.append(parameter)
            # An alternative with no remaining predicate is always true.
            if not pieces:
                return None, []
            groups.append("(" + " AND ".join(pieces) + ")")

        # A single alternative needs no outer parentheses.
        if len(groups) == 1:
            return groups[0], parameters
        return "(" + " OR ".join(groups) + ")", parameters

    @staticmethod
    def _predicate_to_sql(predicate: Predicate, *, present: bool = True) -> tuple[str | None, Any]:
        """Translate a storage-agnostic predicate into SQL.

        A predicate on a column absent from the files behaves as if the column were
        entirely ``NULL``: null checks hold, not-null checks and equalities never match.

        Args:
            predicate: The predicate to translate.
            present: Whether the column exists in the selected files.

        Returns:
            The SQL condition (``None`` when trivially true) and the parameter to bind
            (``None`` for null checks).

        Raises:
            QueryExecutionError: If the operator is not supported.
        """
        column = _quote_identifier(predicate.column)

        # Null checks need no parameter; on an absent column they are always true.
        if predicate.operator is PredicateOperator.IS_NULL:
            return (f"{column} IS NULL" if present else None), None
        # Not-null checks need no parameter; on an absent column they never hold.
        if predicate.operator is PredicateOperator.IS_NOT_NULL:
            return (f"{column} IS NOT NULL" if present else "FALSE"), None
        # Equality binds the value as a parameter; on an absent column it never holds.
        if predicate.operator is PredicateOperator.EQUALS:
            return (f"{column} = ?" if present else "FALSE"), (predicate.value if present else None)
        raise QueryExecutionError(f"Unsupported predicate operator '{predicate.operator}'.")

    @staticmethod
    def _build_order_by(dataset: DatasetDefinition, available_columns: list[str]) -> str:
        """Build the ``ORDER BY`` clause guaranteeing stable pagination.

        Args:
            dataset: Dataset declaring the sort fields.
            available_columns: Columns present in the matched files.

        Returns:
            The ``ORDER BY`` clause with a leading space, or an empty string.
        """
        # Explicit fields are always projected (as NULL when absent), so any of them sorts.
        if dataset.has_explicit_fields:
            sort_fields = list(dataset.order_by)
        # Automatic fields can only sort by columns that actually exist in the files.
        else:
            sort_fields = [name for name in dataset.order_by if name in available_columns]

        # Without usable sort fields DuckDB returns rows in scan order.
        if not sort_fields:
            return ""
        return " ORDER BY " + ", ".join(_quote_identifier(name) for name in sort_fields)

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
    def _expand_files(cursor: duckdb.DuckDBPyConnection, uris: list[str]) -> list[str]:
        """Expand every URI or glob into the concrete files that exist.

        Args:
            cursor: Cursor to execute on.
            uris: Resolved URIs or globs.

        Returns:
            Distinct matching file URIs, in a stable order.
        """
        files: list[str] = []

        # Glob each path separately so a path without matches does not fail the others.
        for uri in uris:
            try:
                rows = cursor.execute("SELECT file FROM glob(?) ORDER BY file", [uri]).fetchall()
            except duckdb.IOException as error:
                # DuckDB reports an empty glob as an IO error on some backends.
                if "No files found" in str(error):
                    continue
                raise
            # Keep each file once even when several globs overlap.
            for row in rows:
                if row[0] not in files:
                    files.append(row[0])
        return files

    @staticmethod
    def _describe_columns(cursor: duckdb.DuckDBPyConnection, files: list[str]) -> dict[str, str]:
        """Return the union of columns across the matched files with their DuckDB types.

        Args:
            cursor: Cursor to execute on.
            files: Resolved file URIs.

        Returns:
            Column name -> DuckDB type, in file order.
        """
        rows = cursor.execute(
            "DESCRIBE SELECT * FROM read_parquet(?, hive_partitioning=true, union_by_name=true)",
            [files],
        ).fetchall()
        return {row[0]: str(row[1]) for row in rows}

    @staticmethod
    def _automatic_columns(
        cursor: duckdb.DuckDBPyConnection,
        files: list[str],
        dataset: DatasetDefinition,
        available_columns: list[str],
    ) -> list[str]:
        """Choose the columns returned by a dataset without explicit response fields.

        Args:
            cursor: Cursor to execute on.
            files: Resolved file URIs.
            dataset: Dataset being queried.
            available_columns: Columns exposed with Hive partitioning enabled.

        Returns:
            The file columns in file order, followed by the declared partition keys that
            only exist as Hive folder values.
        """
        rows = cursor.execute(
            "DESCRIBE SELECT * FROM read_parquet(?, hive_partitioning=false, union_by_name=true)",
            [files],
        ).fetchall()
        excluded = set(dataset.excluded_columns)
        file_columns = [row[0] for row in rows if row[0] not in excluded]
        declared = {partition.name for partition in dataset.partitions}
        # Hive-only columns are kept only when the catalog declares them as partitions.
        extra = [
            name
            for name in available_columns
            if name not in file_columns and name in declared and name not in excluded
        ]
        return file_columns + extra

    def _count_rows(
        self, query: TabularQuery, files: list[str], column_types: dict[str, str]
    ) -> int:
        """Count the rows matching ``query`` (used when a page beyond the end is requested).

        Args:
            query: The validated query.
            files: Resolved file URIs.
            column_types: Column name -> DuckDB type for the columns present in the files.

        Returns:
            Total number of matching rows.
        """
        cursor = self._get_connection().cursor()
        try:
            sql, parameters = self._build_sql(query, files, column_types, count_only=True)
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
            The record, with NaN values converted to ``None``, decimals to floats and
            dates/timestamps to ISO 8601 strings.
        """
        record: dict[str, Any] = {}

        # Pair each value with its column name, sanitizing values JSON/CSV cannot encode.
        for name, value in zip(columns, row, strict=True):
            # NaN and infinities are not valid JSON numbers.
            if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
                value = None
            # Decimals are rendered as floats for client friendliness.
            elif isinstance(value, Decimal):
                value = float(value)
            # Dates and timestamps are serialized as ISO 8601 text.
            elif isinstance(value, (dt.date, dt.datetime)):
                value = value.isoformat()
            record[name] = value
        return record
