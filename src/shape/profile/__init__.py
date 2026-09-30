from .datetime import DatetimeProfile as DatetimeProfile
from .numeric import NumericProfile as NumericProfile
from .sketches import KLL as KLL
from .sketches import HyperLogLog as HyperLogLog
from .sketches import SpaceSaving as SpaceSaving
from .text import TextProfile as TextProfile

__all__ = ["NumericProfile", "TextProfile", "DatetimeProfile", "KLL", "HyperLogLog", "SpaceSaving"]
from shape._callable import make_callable  # noqa: E402

from .advanced import (
    MissingnessEvidence as MissingnessEvidence,
)
from .advanced import (
    entropy as entropy,
)
from .advanced import (
    infer_pattern as infer_pattern,
)
from .advanced import (
    missingness_dependency as missingness_dependency,
)
from .advanced import (
    pearson as pearson,
)
from .bounded_dependencies import (
    BoundedFDEvidence as BoundedFDEvidence,
)
from .bounded_dependencies import (
    bounded_functional_dependency as bounded_functional_dependency,
)
from .dependence import (
    conditional_numeric_means as conditional_numeric_means,
)
from .dependence import (
    normalized_mutual_information as normalized_mutual_information,
)
from .dependencies import (
    CandidateKeyEvidence as CandidateKeyEvidence,
)
from .dependencies import (
    FunctionalDependencyEvidence as FunctionalDependencyEvidence,
)
from .dependencies import (
    candidate_key as candidate_key,
)
from .dependencies import (
    functional_dependency as functional_dependency,
)
from .error import ErrorModel as ErrorModel
from .error import hll_error as hll_error
from .error import kll_error as kll_error
from .temporal import lag_autocorrelation as lag_autocorrelation
from .text_vectorized import (
    profile_text_array as profile_text_array,
)
from .text_vectorized import (
    semantic_detect_array as semantic_detect_array,
)

make_callable(__name__, "profile")  # shape.profile(source) is also the public function
