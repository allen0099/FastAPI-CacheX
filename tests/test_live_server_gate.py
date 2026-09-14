"""The gate that stops CI passing with the live-server suites skipped.

`tests/live_servers.py` makes the Redis and Memcached suites opt-in so that a
plain `uv run pytest` cannot wipe a developer's own server. The cost of that
design is that a mistyped port, a service container that never came up, or a
missing extra turns those suites into *skips* -- and a skip is green.

Coverage does not catch it either. With only the Memcached suite skipped the
run still reports ~96.9%, comfortably over the `fail_under = 90` gate, so the
threshold cannot be used as the backstop here.

So the workflows, where the services are started by the workflow itself and are
therefore guaranteed, set `CACHEX_REQUIRE_LIVE_SERVERS=1`, and the first test
below turns "would be skipped" back into a failure that names the reason.
"""

import pytest

from tests.live_servers import REQUIRE_LIVE_SERVERS_ENV
from tests.live_servers import UNCONNECTED_PORT
from tests.live_servers import live_server_gate_failures
from tests.live_servers import live_servers_required


def test_live_server_suites_actually_run_when_they_are_required() -> None:
    """Where the servers are guaranteed, a skipped suite is a failed run."""
    if not live_servers_required():
        pytest.skip(
            f"{REQUIRE_LIVE_SERVERS_ENV} is not set; skipping the live-server "
            "suites is allowed here",
        )

    failures = live_server_gate_failures()
    if failures:
        reasons = "\n".join(f"  - {reason}" for reason in failures)
        pytest.fail(
            f"{REQUIRE_LIVE_SERVERS_ENV} is set, so the Redis and Memcached "
            f"suites must run, but they would be skipped:\n{reasons}",
        )


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "TRUE", " 1 "])
def test_the_requirement_is_on_for_the_usual_truthy_values(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv(REQUIRE_LIVE_SERVERS_ENV, value)
    assert live_servers_required() is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "FALSE"])
def test_the_requirement_is_off_for_unset_and_explicit_negatives(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv(REQUIRE_LIVE_SERVERS_ENV, value)
    assert live_servers_required() is False


def test_the_requirement_is_off_when_the_variable_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(REQUIRE_LIVE_SERVERS_ENV, raising=False)
    assert live_servers_required() is False


def test_not_opting_in_is_reported_as_a_failure_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The no-port case names the variable to set, not just "skipped"."""
    monkeypatch.setattr("tests.live_servers.REDIS_OPTED_IN", False)
    monkeypatch.setattr("tests.live_servers.MEMCACHED_OPTED_IN", False)

    failures = live_server_gate_failures()

    assert any("CACHEX_TEST_REDIS_PORT" in reason for reason in failures)
    assert any("CACHEX_TEST_MEMCACHED_PORT" in reason for reason in failures)


def test_a_port_with_nothing_behind_it_is_reported_as_a_failure_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The mistyped-port case is the one CI is most likely to hit."""
    monkeypatch.setattr("tests.live_servers.REDIS_OPTED_IN", True)
    monkeypatch.setattr("tests.live_servers.REDIS_PORT", UNCONNECTED_PORT)
    monkeypatch.setattr("tests.live_servers.MEMCACHED_OPTED_IN", True)
    monkeypatch.setattr("tests.live_servers.MEMCACHED_PORT", UNCONNECTED_PORT)

    failures = live_server_gate_failures()

    assert any("nothing is listening" in reason for reason in failures)


def test_a_missing_redis_package_is_reported_as_a_failure_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A server that is up but a client that was never installed still skips."""
    monkeypatch.setattr("tests.live_servers.has_redis_package", lambda: False)

    failures = live_server_gate_failures()

    assert any("redis package is not installed" in reason for reason in failures)
