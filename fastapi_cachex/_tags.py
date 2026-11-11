"""Tag versions: invalidate everything written under a tag at once (#538).

Each tag has a version, a random token stored in the backend under
``tag:<name>``. An entry written with tags records the version each tag had
when it was written, and is served only while every one of them is still the
current version. Invalidating a tag gives it a new version, so every entry
written under the old one reads as a miss; they are left to expire.

This needs no key enumeration, so it works on Memcached as well. A tag whose
version is lost (expired after ``TAG_VERSION_TTL``, evicted, or the server
restarted) gets a new one on the next write, which also invalidates what was
written under it: losing state can only cost a miss, never serve an
invalidated entry.
"""

import secrets
from collections.abc import Iterable
from collections.abc import Mapping

from .backends.base import BaseCacheBackend
from .types import CacheEntry
from .types import TagVersions

TAG_KEY_PREFIX = "tag:"
# How long a version is kept. Without an expiry a tag per user or per item
# would leave a key behind for good. When a version expires, what was written
# under it reads as a miss and the next write starts a new one, so a tagged
# entry lives at most this long whatever its own ttl.
TAG_VERSION_TTL = 30 * 24 * 60 * 60
_TAG_FINGERPRINT = "tag"


def _tag_key(name: str) -> str:
    return f"{TAG_KEY_PREFIX}{name}"


def _version_entry() -> CacheEntry:
    """A new version: a token no earlier version of the tag had."""
    return CacheEntry(
        fingerprint=_TAG_FINGERPRINT, content=secrets.token_hex(8).encode("ascii")
    )


def _version_of(entry: CacheEntry | None) -> str | None:
    return None if entry is None else entry.content.decode("ascii", "replace")


def validate_tags(tags: object) -> tuple[str, ...]:
    """``tags`` as a tuple of distinct, non-empty names, in a stable order.

    Raises:
        TypeError: If ``tags`` is a single ``str`` (it would be read as one
            tag per character), or holds something that is not a ``str``.
        ValueError: If a name is empty.
    """
    if tags is None:
        return ()
    if isinstance(tags, (str, bytes)) or not isinstance(tags, Iterable):
        msg = "tags must be an iterable of tag names, not a single str"
        raise TypeError(msg)
    names = tuple(tags)
    for name in names:
        if not isinstance(name, str):
            msg = f"a tag must be a str, got {type(name).__name__}"
            raise TypeError(msg)
        if not name:
            msg = "a tag cannot be empty"
            raise ValueError(msg)
    return tuple(sorted(set(names)))


# The version each tag had before a value was computed; None for a tag that
# had none yet.
VersionsBefore = dict[str, str | None]


async def versions_before(
    backend: BaseCacheBackend, tags: Iterable[str]
) -> VersionsBefore:
    """The version of each tag, read before the value is computed.

    Before, not after: a value computed from data that an invalidation has
    since replaced must not be stored under the version that invalidation
    set. Nothing is created here, so a request that stores nothing (a 404, a
    failing factory) leaves no key behind.
    """
    names = tuple(tags)
    if not names:
        return {}
    found = await backend.get_many(_tag_key(name) for name in names)
    return {name: _version_of(found.get(_tag_key(name))) for name in names}


async def versions_to_record(
    backend: BaseCacheBackend, before: VersionsBefore
) -> TagVersions | None:
    """What an entry records as it is stored, or None if it must not be.

    A tag that had no version before the value was computed gets one now. If
    someone else gave it one meanwhile, that may have been an invalidation,
    so the value is not stored: the next request computes it again.
    """
    versions: list[tuple[str, str]] = []
    for name, version in before.items():
        if version is not None:
            versions.append((name, version))
            continue
        fresh = _version_entry()
        if not await backend.set_if_absent(_tag_key(name), fresh, ttl=TAG_VERSION_TTL):
            return None
        versions.append((name, fresh.content.decode("ascii")))
    return tuple(versions)


async def current_versions(
    backend: BaseCacheBackend, tags: Iterable[str]
) -> TagVersions:
    """The versions to record for a value that is written as it is given.

    ``CacheManager.set()``: there is nothing to compute, so nothing can be
    invalidated in between. A tag that gets its first version from another
    writer at this very moment is read again.
    """
    names = tuple(tags)
    versions = await versions_to_record(backend, await versions_before(backend, names))
    if versions is None:
        versions = await versions_to_record(
            backend, await versions_before(backend, names)
        )
    # Twice in a row is a tag that keeps disappearing: versions nothing matches.
    return versions or tuple((name, secrets.token_hex(8)) for name in names)


async def outdated(
    backend: BaseCacheBackend, entries: Mapping[str, CacheEntry]
) -> set[str]:
    """The keys of ``entries`` whose tags have been invalidated since.

    One batched read for all of them; none at all when no entry has tags.
    """
    names = {name for entry in entries.values() for name, _ in entry.tags}
    if not names:
        return set()
    found = await backend.get_many(_tag_key(name) for name in names)
    current = {name: _version_of(found.get(_tag_key(name))) for name in names}
    return {
        key
        for key, entry in entries.items()
        if any(current[name] != version for name, version in entry.tags)
    }


async def invalidate(backend: BaseCacheBackend, tags: Iterable[str]) -> None:
    """Give each tag a new version: what was written under them is a miss."""
    names = validate_tags(tags)
    if names:
        await backend.set_many(
            {_tag_key(name): _version_entry() for name in names},
            ttl=TAG_VERSION_TTL,
        )
