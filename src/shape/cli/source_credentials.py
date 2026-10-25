"""Reference-only, command-scoped credentials for CLI cloud readers."""

from collections.abc import Sequence

from shape.security import credrefs

KEYS = frozenset({"token", "account_key", "sas_token", "connection_string", "credentials"})


def extract(argv: Sequence[str]) -> tuple[list[str], dict[str, str]]:
    """Remove the global option wherever it occurs, including plugin commands."""
    remaining: list[str] = []
    options: dict[str, str] = {}
    items = iter(argv)
    for item in items:
        if item == "--":
            remaining.append(item)
            remaining.extend(items)
            break
        if item == "--source-config":
            value = next(items, None)
            if value is None:
                raise ValueError("--source-config needs KEY=REF")
        elif item.startswith("--source-config="):
            value = item.split("=", 1)[1]
        else:
            remaining.append(item)
            continue
        key, separator, reference = value.partition("=")
        if not separator or key not in KEYS:
            raise ValueError(
                "--source-config needs token, account_key, sas_token, "
                "connection_string or credentials=REF"
            )
        if credrefs.scheme_of(reference) not in ("env", "file", "kv"):
            raise ValueError(
                "--source-config takes env://, file:// or kv:// references, never a literal secret"
            )
        if key in options:
            raise ValueError(f"--source-config repeats {key}")
        if key == "credentials":
            if credrefs.scheme_of(reference) not in ("env", "file"):
                raise ValueError("BigQuery credentials need an env:// or file:// reference")
            credrefs.check_form(reference)
            options[key] = reference
        else:
            options[key] = credrefs.resolve_reference(reference)
    return remaining, options
