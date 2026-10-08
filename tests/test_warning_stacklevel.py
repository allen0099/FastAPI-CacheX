"""Warnings name the application's line, whatever path raised them (#333).

A fixed ``stacklevel`` is right for one call depth only: a backend method
called directly, through ``CacheManager`` or through a base-class fallback
sits at a different depth each time, and the warning named a library file.
"""

import inspect

import pytest

from fastapi_cachex._warnings import caller_stacklevel
from fastapi_cachex.backends.memory import MemoryBackend


def test_caller_stacklevel_without_frame_support(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(inspect, "currentframe", lambda: None)
    assert caller_stacklevel() == 2


async def test_a_path_shaped_pattern_warning_names_the_caller() -> None:
    with pytest.warns(RuntimeWarning, match="cleared nothing") as record:
        await MemoryBackend().clear_pattern("/users/*")

    assert record[0].filename == __file__
