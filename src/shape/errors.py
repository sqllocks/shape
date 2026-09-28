"""Typed public errors for Shape."""


class ShapeError(Exception):
    """Base class for expected public Shape failures."""


class ShapeTypeError(ShapeError):
    """A physical/logical type cannot be represented safely."""


class ShapeSchemaError(ShapeError):
    """A schema or batch violates a Shape contract."""


class ShapeSecurityError(ShapeError):
    """A security, sensitivity, or trust policy is violated."""


class ShapeCapabilityError(ShapeError):
    """A requested operation requires an unavailable capability."""
