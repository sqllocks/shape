"""The generation package.

Names are imported on first use (PEP 562): importing ``shape.generation.engine`` or any other
submodule does not load the fidelity, certificate and profiling modules behind the rest of this
list, which keeps ``shape generate`` start-up short (P4-10). ``from shape.generation import X``
and ``shape.generation.X`` work as before.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .address_vectorized import CompiledAddressAsset as CompiledAddressAsset
    from .address_vectorized import generate_addresses as generate_addresses
    from .advanced import CorrelatedNormal as CorrelatedNormal
    from .advanced import Empirical as Empirical
    from .advanced import UniqueToken as UniqueToken
    from .certificate import MetricScore as MetricScore
    from .certificate import certify as certify
    from .compiler import GenerationReport as GenerationReport
    from .compiler import generate_from_shape as generate_from_shape
    from .compiler import generate_relational as generate_relational
    from .core import compare_numeric as compare_numeric
    from .core import generate_numeric as generate_numeric
    from .distribution_fidelity import DistributionFidelity as DistributionFidelity
    from .distribution_fidelity import categorical_fidelity as categorical_fidelity
    from .distribution_fidelity import quantile_fidelity as quantile_fidelity
    from .evolution import ShapePoint as ShapePoint
    from .evolution import interpolate as interpolate
    from .fidelity import FidelityCertificate as FidelityCertificate
    from .fidelity import FidelityDimension as FidelityDimension
    from .fidelity import FidelityResult as FidelityResult
    from .fidelity import PlanItem as PlanItem
    from .fidelity import ReconstructionPlan as ReconstructionPlan
    from .fidelity import certify_shapes as certify_shapes
    from .fidelity import evaluate_fidelity as evaluate_fidelity
    from .fidelity import plan_reconstruction as plan_reconstruction
    from .geo_fidelity import GeoFidelity as GeoFidelity
    from .geo_fidelity import geographic_fidelity as geographic_fidelity
    from .hierarchy import HierarchicalSampler as HierarchicalSampler
    from .hierarchy import hierarchy_violations as hierarchy_violations
    from .joint import JointModel as JointModel
    from .joint import fit_joint_numeric as fit_joint_numeric
    from .joint import generate_joint_numeric as generate_joint_numeric
    from .joint_model import ChowLiuModel as ChowLiuModel
    from .joint_model import fit_joint as fit_joint
    from .joint_model import joint_fidelity as joint_fidelity
    from .levels import LEVELS as LEVELS
    from .levels import FidelityLevel as FidelityLevel
    from .levels import assess_fidelity as assess_fidelity
    from .relational import ParentChildSpec as ParentChildSpec
    from .relational import generate_children as generate_children
    from .relational import scd2_versions as scd2_versions
    from .relational_fidelity import RelationalFidelity as RelationalFidelity
    from .relational_fidelity import relational_fidelity as relational_fidelity
    from .release import ReleaseDecision as ReleaseDecision
    from .release import release_decision as release_decision
    from .strategies import Choice as Choice
    from .strategies import Conditional as Conditional
    from .strategies import Constant as Constant
    from .strategies import Derived as Derived
    from .strategies import FirstPerParent as FirstPerParent
    from .strategies import ForeignKey as ForeignKey
    from .strategies import GenerationPlan as GenerationPlan
    from .strategies import Normal as Normal
    from .strategies import SequenceStrategy as SequenceStrategy
    from .strategies import Strategy as Strategy
    from .strategies import Uniform as Uniform
    from .timeline import ShapeTimeline as ShapeTimeline
    from .timeline import VersionedShape as VersionedShape
    from .vectorized import composite_keys as composite_keys
    from .vectorized import foreign_keys as foreign_keys
    from .vectorized import parent_child_keys as parent_child_keys

_EXPORTS = {
    "CompiledAddressAsset": ("address_vectorized", "CompiledAddressAsset"),
    "generate_addresses": ("address_vectorized", "generate_addresses"),
    "CorrelatedNormal": ("advanced", "CorrelatedNormal"),
    "Empirical": ("advanced", "Empirical"),
    "UniqueToken": ("advanced", "UniqueToken"),
    "MetricScore": ("certificate", "MetricScore"),
    "certify": ("certificate", "certify"),
    "GenerationReport": ("compiler", "GenerationReport"),
    "generate_from_shape": ("compiler", "generate_from_shape"),
    "generate_relational": ("compiler", "generate_relational"),
    "compare_numeric": ("core", "compare_numeric"),
    "generate_numeric": ("core", "generate_numeric"),
    "DistributionFidelity": ("distribution_fidelity", "DistributionFidelity"),
    "categorical_fidelity": ("distribution_fidelity", "categorical_fidelity"),
    "quantile_fidelity": ("distribution_fidelity", "quantile_fidelity"),
    "ShapePoint": ("evolution", "ShapePoint"),
    "interpolate": ("evolution", "interpolate"),
    "FidelityCertificate": ("fidelity", "FidelityCertificate"),
    "FidelityDimension": ("fidelity", "FidelityDimension"),
    "FidelityResult": ("fidelity", "FidelityResult"),
    "PlanItem": ("fidelity", "PlanItem"),
    "ReconstructionPlan": ("fidelity", "ReconstructionPlan"),
    "certify_shapes": ("fidelity", "certify_shapes"),
    "evaluate_fidelity": ("fidelity", "evaluate_fidelity"),
    "plan_reconstruction": ("fidelity", "plan_reconstruction"),
    "GeoFidelity": ("geo_fidelity", "GeoFidelity"),
    "geographic_fidelity": ("geo_fidelity", "geographic_fidelity"),
    "HierarchicalSampler": ("hierarchy", "HierarchicalSampler"),
    "hierarchy_violations": ("hierarchy", "hierarchy_violations"),
    "ChowLiuModel": ("joint_model", "ChowLiuModel"),
    "fit_joint": ("joint_model", "fit_joint"),
    "joint_fidelity": ("joint_model", "joint_fidelity"),
    "JointModel": ("joint", "JointModel"),
    "fit_joint_numeric": ("joint", "fit_joint_numeric"),
    "generate_joint_numeric": ("joint", "generate_joint_numeric"),
    "FidelityLevel": ("levels", "FidelityLevel"),
    "LEVELS": ("levels", "LEVELS"),
    "assess_fidelity": ("levels", "assess_fidelity"),
    "ParentChildSpec": ("relational", "ParentChildSpec"),
    "generate_children": ("relational", "generate_children"),
    "scd2_versions": ("relational", "scd2_versions"),
    "RelationalFidelity": ("relational_fidelity", "RelationalFidelity"),
    "relational_fidelity": ("relational_fidelity", "relational_fidelity"),
    "ReleaseDecision": ("release", "ReleaseDecision"),
    "release_decision": ("release", "release_decision"),
    "Choice": ("strategies", "Choice"),
    "Conditional": ("strategies", "Conditional"),
    "Constant": ("strategies", "Constant"),
    "Derived": ("strategies", "Derived"),
    "FirstPerParent": ("strategies", "FirstPerParent"),
    "ForeignKey": ("strategies", "ForeignKey"),
    "GenerationPlan": ("strategies", "GenerationPlan"),
    "Normal": ("strategies", "Normal"),
    "SequenceStrategy": ("strategies", "SequenceStrategy"),
    "Strategy": ("strategies", "Strategy"),
    "Uniform": ("strategies", "Uniform"),
    "ShapeTimeline": ("timeline", "ShapeTimeline"),
    "VersionedShape": ("timeline", "VersionedShape"),
    "composite_keys": ("vectorized", "composite_keys"),
    "foreign_keys": ("vectorized", "foreign_keys"),
    "parent_child_keys": ("vectorized", "parent_child_keys"),
}

__all__ = [
    "FidelityResult",
    "compare_numeric",
    "generate_numeric",
    "Strategy",
    "Constant",
    "SequenceStrategy",
    "Choice",
    "Uniform",
    "Normal",
    "Conditional",
    "Derived",
    "ForeignKey",
    "FirstPerParent",
    "GenerationPlan",
    "ParentChildSpec",
    "generate_children",
    "scd2_versions",
    "evaluate_fidelity",
    "FidelityLevel",
    "LEVELS",
    "assess_fidelity",
    "ShapePoint",
    "interpolate",
    "Empirical",
    "CorrelatedNormal",
    "UniqueToken",
    "MetricScore",
    "FidelityCertificate",
    "certify",
    "RelationalFidelity",
    "relational_fidelity",
    "GeoFidelity",
    "geographic_fidelity",
    "ReleaseDecision",
    "release_decision",
    "DistributionFidelity",
    "categorical_fidelity",
    "quantile_fidelity",
    "composite_keys",
    "foreign_keys",
    "parent_child_keys",
    "CompiledAddressAsset",
    "generate_addresses",
    "GenerationReport",
    "generate_from_shape",
    "generate_relational",
    "VersionedShape",
    "ShapeTimeline",
]


def __getattr__(name: str) -> Any:
    try:
        module, attribute = _EXPORTS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    value = getattr(importlib.import_module(f".{module}", __name__), attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_EXPORTS})
