from shape._callable import make_callable  # noqa: E402

from .core import ShapeQueryError as ShapeQueryError
from .core import ShapeView as ShapeView
from .core import query as query

make_callable(__name__, "query")  # shape.query(shape, expression) is also the public function
