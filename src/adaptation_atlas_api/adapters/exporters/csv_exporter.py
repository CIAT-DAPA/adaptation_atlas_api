"""CSV exporter producing the same logical records and fields as the JSON response."""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator

from adaptation_atlas_api.domain.models.results import TabularResult
from adaptation_atlas_api.ports.exporter import TabularExporter


class CsvExporter(TabularExporter):
    """Streams a :class:`TabularResult` as RFC 4180 CSV with a header row."""

    media_type = "text/csv"
    file_extension = "csv"

    def __init__(self, chunk_rows: int = 500) -> None:
        """Create the exporter.

        Args:
            chunk_rows: Number of rows serialized per emitted chunk.
        """
        self._chunk_rows = max(1, chunk_rows)

    def export(self, result: TabularResult) -> Iterator[str]:
        """Serialize the header and the records in chunks.

        Args:
            result: The result to serialize.

        Yields:
            CSV text chunks; the first one always contains the header row.
        """
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=result.columns, extrasaction="ignore")
        writer.writeheader()
        yield self._flush(buffer)

        # Emit the records in fixed size chunks to bound memory usage.
        for start in range(0, len(result.records), self._chunk_rows):
            # Each slice is written to the buffer and flushed as one chunk.
            for record in result.records[start : start + self._chunk_rows]:
                writer.writerow(record)
            yield self._flush(buffer)

    @staticmethod
    def _flush(buffer: io.StringIO) -> str:
        """Return the buffered text and reset the buffer.

        Args:
            buffer: Buffer used by the CSV writer.

        Returns:
            The text accumulated since the previous flush.
        """
        text = buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)
        return text
