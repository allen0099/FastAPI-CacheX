"""Response header helpers for ``@cache``."""

from starlette.datastructures import MutableHeaders


def add_vary(headers: MutableHeaders, names: list[str]) -> None:
    """Add header names to ``Vary``, keeping the values already there.

    The missing names go on one new ``Vary`` line, so every line already
    there is kept (Starlette's ``add_vary_header`` rewrites the first line and
    drops the others). Names the response already varies on (compared
    case-insensitively) are skipped, as is everything when it already varies
    on ``*``.

    Args:
        headers: Mutable response headers to write to
        names: Request header names the response depends on
    """
    present = {
        value.strip().lower()
        for line in headers.getlist("vary")
        for value in line.split(",")
    }
    if "*" in present:
        return
    missing: list[str] = []
    for name in names:
        if name.lower() not in present:
            missing.append(name)
            present.add(name.lower())
    if missing:
        headers.append("Vary", ", ".join(missing))
