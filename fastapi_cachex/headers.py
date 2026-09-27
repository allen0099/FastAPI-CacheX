"""Response header helpers shared by ``@cache`` and the session middleware."""

from starlette.datastructures import MutableHeaders


def add_vary(headers: MutableHeaders, names: list[str]) -> None:
    """Add header names to ``Vary``, keeping the values already there.

    Starlette's ``add_vary_header`` appends unconditionally, so names the
    response already varies on (compared case-insensitively) are skipped, as
    is everything when it already varies on ``*``.

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
    for name in names:
        if name.lower() not in present:
            headers.add_vary_header(name)
            present.add(name.lower())
