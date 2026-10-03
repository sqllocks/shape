"""Input: turn any supported source into a stream of Arrow record batches."""

from .excel import WorkbookError as WorkbookError
from .readers import (
    PANDAS_CSV as PANDAS_CSV,
)
from .readers import (
    CsvOptions as CsvOptions,
)
from .readers import (
    ReaderError as ReaderError,
)
from .readers import (
    Source as Source,
)
from .readers import (
    expand_paths as expand_paths,
)
from .readers import (
    file_kind as file_kind,
)
from .readers import (
    iter_rows as iter_rows,
)
from .readers import (
    open_source as open_source,
)
from .readers import (
    open_workbook as open_workbook,
)
from .readers import (
    read_batches as read_batches,
)
from .readers import (
    read_table as read_table,
)
from .readers import (
    sniff_delimiter as sniff_delimiter,
)

__all__ = [
    "PANDAS_CSV",
    "CsvOptions",
    "ReaderError",
    "WorkbookError",
    "Source",
    "expand_paths",
    "file_kind",
    "iter_rows",
    "open_source",
    "open_workbook",
    "read_batches",
    "read_table",
    "sniff_delimiter",
]
