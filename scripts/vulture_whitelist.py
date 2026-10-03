"""Names vulture reports as unused that are part of a public interface.

Each entry must say why it is not dead code. Keep this list short; never add a name to hide
real dead code.
"""

# shape.plugins.api.v1: `uri` is a parameter of the Protocol stubs (Source.can_open/schema/read,
# Sink.write, Emitter.emit, StreamSource.read). Implementations use it; the stubs cannot.
uri  # noqa: B018, F821

# argparse.Action.__call__ signature: `option_string` is passed by argparse and unused by the
# `--rate` action in shape.cli.emit, which only records that the option was given.
option_string  # noqa: B018, F821
