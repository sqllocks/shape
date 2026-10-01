"""Names vulture reports as unused that are part of a public interface.

Each entry must say why it is not dead code. Keep this list short; never add a name to hide
real dead code.
"""

# shape.plugins.api.v1: `uri` is a parameter of the Protocol stubs (Source.can_open/schema/read,
# Sink.write, Emitter.emit, StreamSource.read). Implementations use it; the stubs cannot.
uri  # noqa: B018, F821
