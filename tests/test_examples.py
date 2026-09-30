"""Run every app in `examples/` through its main flow.

The examples are documentation, so these tests keep them working: each one is
loaded fresh from its file, started with its lifespan, and driven through
`TestClient`. A `DeprecationWarning` fails the test, so an example cannot keep
showing an API we are phasing out.

`session_jwt`, `session_jwt_claims`, `redis_backend` and `session_redis` need
optional packages and are skipped without them (checked with `find_spec`, never
imported here). `redis_backend` and `session_redis` also talk to a real server,
so they follow the opt-in rules in `tests/live_servers.py`.
"""

import importlib.util
import sys
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import ModuleType
from urllib.parse import parse_qs
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from fastapi_cachex.exceptions import BackendNotFoundError
from fastapi_cachex.manager_proxy import CacheManagerProxy
from fastapi_cachex.proxy import BackendProxy
from fastapi_cachex.session.exceptions import SessionSecurityError
from fastapi_cachex.session.proxy import SessionManagerProxy
from fastapi_cachex.state.proxy import StateManagerProxy
from tests.live_servers import REDIS_HOST
from tests.live_servers import REDIS_PORT
from tests.live_servers import redis_skip_reason

pytestmark = pytest.mark.filterwarnings("error::DeprecationWarning")

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"
_PROXIES = (BackendProxy, CacheManagerProxy, SessionManagerProxy, StateManagerProxy)


@pytest.fixture(autouse=True)
def _reset_proxies() -> Iterator[None]:
    """Every example registers its own backend and managers at import."""
    for proxy in _PROXIES:
        proxy.set(None)
    yield
    for proxy in _PROXIES:
        proxy.set(None)


@pytest.fixture(autouse=True)
def _session_secret_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without SESSION_SECRET_KEY the session examples warn (an error here)."""
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-" + "k" * 43)


def load_example(name: str) -> ModuleType:
    """Import `examples/<name>.py` as a new module, so no state is shared."""
    module_name = f"_cachex_example_{name}"
    spec = importlib.util.spec_from_file_location(
        module_name, EXAMPLES_DIR / f"{name}.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        del sys.modules[module_name]
    return module


def test_every_example_has_a_test() -> None:
    """A new example must be added to this file (and to examples/README.md)."""
    tested = {
        "app_cache",
        "cache_lock",
        "http_cache",
        "oauth_state",
        "rate_limit",
        "redis_backend",
        "session_api",
        "session_jwt",
        "session_jwt_claims",
        "session_login",
        "session_redis",
    }
    assert {p.stem for p in EXAMPLES_DIR.glob("*.py")} == tested
    readme = (EXAMPLES_DIR / "README.md").read_text(encoding="utf-8")
    for name in tested:
        assert f"{name}.py" in readme


def test_http_cache() -> None:
    example = load_example("http_cache")
    with TestClient(example.app) as client:
        first = client.get("/products/1")
        second = client.get("/products/1")
        assert first.status_code == second.status_code == 200
        assert (
            first.json()
            == second.json()
            == {
                "id": 1,
                "name": "Keyboard",
                "price": 49,
            }
        )
        # Miss, then hit: the handler ran once.
        assert example.handler_runs["product"] == 1
        assert first.headers["cache-control"] == "max-age=60"

        etag = first.headers["etag"]
        not_modified = client.get("/products/1", headers={"If-None-Match": etag})
        assert not_modified.status_code == 304
        assert example.handler_runs["product"] == 1

        # The update clears the cached copy.
        assert client.put("/products/1", params={"price": 59}).status_code == 200
        assert client.get("/products/1").json()["price"] == 59
        assert example.handler_runs["product"] == 2

        stock = client.get("/stock/1")
        assert stock.headers["cache-control"] == "no-cache"
        prefs = client.get("/me/preferences")
        assert "private" in prefs.headers["cache-control"]


def test_http_cache_monitoring_routes_need_the_admin_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    example = load_example("http_cache")
    with TestClient(example.app) as client:
        monkeypatch.delenv("CACHE_ADMIN_TOKEN", raising=False)
        # Closed while no token is configured, whatever the client sends.
        assert client.get("/_cache/cached-hits").status_code == 403
        assert (
            client.get("/_cache/cached-hits", headers={"X-Admin-Token": ""}).status_code
            == 403
        )

        monkeypatch.setenv("CACHE_ADMIN_TOKEN", "test-admin-token")
        client.get("/products/2")
        assert (
            client.get(
                "/_cache/cached-hits", headers={"X-Admin-Token": "wrong"}
            ).status_code
            == 403
        )
        hits = client.get(
            "/_cache/cached-hits", headers={"X-Admin-Token": "test-admin-token"}
        )
        assert hits.status_code == 200
        assert "/products/2" in hits.text


def test_app_cache() -> None:
    example = load_example("app_cache")
    with TestClient(example.app) as client:
        first = client.get("/rates")
        second = client.get("/rates")
        assert first.json() == second.json() == {"EUR": 0.92, "JPY": 151.3}
        # The factory ran once for two reads.
        assert example.upstream_calls["rates"] == 1

        assert client.delete("/rates").json() == {"deleted": True}
        client.get("/rates")
        assert example.upstream_calls["rates"] == 2

        created = client.post("/orders", headers={"Idempotency-Key": "abc"})
        assert created.status_code == 201
        order_id = created.json()["order_id"]
        retry = client.post("/orders", headers={"Idempotency-Key": "abc"})
        assert retry.status_code == 409
        assert order_id in retry.json()["detail"]
        other = client.post("/orders", headers={"Idempotency-Key": "def"})
        assert other.status_code == 201


def _session_cookie(client: TestClient) -> str:
    token = client.cookies.get("session")
    assert token
    return str(token)


def test_session_login() -> None:
    example = load_example("session_login")
    credentials = {"username": "alice", "password": "alice-demo-password"}
    with TestClient(example.app) as client:
        # An anonymous visitor fills a cart: that starts a session.
        assert client.post("/cart/book").json() == {"cart": ["book"]}
        anonymous_token = _session_cookie(client)
        # An anonymous session is not a login.
        assert client.get("/me").status_code == 401

        wrong = {"username": "alice", "password": "nope"}
        assert client.post("/login", json=wrong).status_code == 401

        assert client.post("/login", json=credentials).status_code == 200
        user_token = _session_cookie(client)
        # Login rotated the session ID: a new token, and the old one is dead.
        assert user_token != anonymous_token
        me = client.get("/me")
        assert me.status_code == 200
        assert me.json() == {"user": "alice", "cart": ["book"]}

        with TestClient(example.app) as stranger:
            assert stranger.get("/me").status_code == 401
            stranger.cookies.set("session", anonymous_token)
            assert stranger.get("/me").status_code == 401
            stranger.cookies.clear()
            # The header transport reaches the same session.
            assert (
                stranger.get("/me", headers={"X-Session-Token": user_token}).status_code
                == 200
            )

        assert client.post("/logout").json() == {"logged_out": True}
        assert client.post("/logout").json() == {"logged_out": False}
        with TestClient(example.app) as replay:
            replay.cookies.set("session", user_token)
            assert replay.get("/me").status_code == 401


def test_session_login_without_a_prior_session() -> None:
    example = load_example("session_login")
    credentials = {"username": "alice", "password": "alice-demo-password"}
    with TestClient(example.app) as client:
        assert client.post("/login", json=credentials).status_code == 200
        assert client.get("/me").json() == {"user": "alice", "cart": []}


def _cookie_attributes(set_cookie: str) -> dict[str, str]:
    """The attributes of one `Set-Cookie` value, keys lowercased, value dropped."""
    _, *parts = (part.strip() for part in set_cookie.split(";"))
    attributes = {}
    for part in parts:
        key, _, value = part.partition("=")
        attributes[key.lower()] = value
    return attributes


def _session_set_cookie(set_cookies: list[str]) -> str:
    """The one `Set-Cookie` value for the session cookie."""
    [value] = [v for v in set_cookies if v.startswith("session=")]
    return value


@pytest.mark.parametrize("returning_visitor", [False, True])
def test_session_login_response_is_private(returning_visitor: bool) -> None:
    """The login response carries a credential: never cacheable, same cookie flags."""
    example = load_example("session_login")
    example.config.cookie_domain = "example.test"
    credentials = {"username": "alice", "password": "alice-demo-password"}
    with TestClient(example.app, base_url="http://app.example.test") as client:
        # A cookie set by the middleware itself: the reference attributes.
        started = client.post("/cart/book")
        expected = _cookie_attributes(
            _session_set_cookie(started.headers.get_list("set-cookie"))
        )
        assert expected == {
            "path": "/",
            "max-age": str(example.config.cookie_max_age),
            "httponly": "",
            "samesite": "lax",
            "domain": "example.test",
        }
        if not returning_visitor:
            client.cookies.clear()

        login = client.post("/login", json=credentials)
        assert login.status_code == 200
        assert login.headers["cache-control"] == "private, no-store"
        set_cookie = _session_set_cookie(login.headers.get_list("set-cookie"))
        assert _cookie_attributes(set_cookie) == expected
        # Only the HttpOnly cookie: a copy in a header would be readable by scripts.
        assert "x-session-token" not in login.headers
        assert client.get("/me").json()["user"] == "alice"

        logout = client.post("/logout")
        cleared = _cookie_attributes(
            _session_set_cookie(logout.headers.get_list("set-cookie"))
        )
        assert logout.headers["cache-control"] == "private, no-store"
        assert cleared["domain"] == expected["domain"]
        assert cleared["path"] == expected["path"]
        assert "expires" in cleared
        assert not client.cookies.get("session")
        assert client.get("/me").status_code == 401


@pytest.mark.parametrize(
    "name",
    [
        "session_api",
        "session_login",
        *(
            pytest.param(
                name,
                marks=pytest.mark.skipif(
                    importlib.util.find_spec("jwt") is None,
                    reason=f"{name} needs the jwt extra (PyJWT)",
                ),
            )
            for name in ("session_jwt", "session_jwt_claims")
        ),
    ],
)
def test_session_example_warns_without_a_secret_key(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """No placeholder key: an unset SESSION_SECRET_KEY warns and a random one is used."""
    monkeypatch.delenv("SESSION_SECRET_KEY")
    with pytest.warns(UserWarning, match="SESSION_SECRET_KEY is not set") as record:
        first = load_example(name)
    assert record[0].filename == str(EXAMPLES_DIR / f"{name}.py")
    with pytest.warns(UserWarning, match="SESSION_SECRET_KEY is not set"):
        second = load_example(name)
    assert len(first.config.secret_key.get_secret_value()) >= 32
    assert (
        first.config.secret_key.get_secret_value()
        != second.config.secret_key.get_secret_value()
    )


@pytest.mark.skipif(
    importlib.util.find_spec("jwt") is None,
    reason="session_jwt needs the jwt extra (PyJWT)",
)
def test_session_jwt() -> None:
    example = load_example("session_jwt")
    credentials = {"username": "alice", "password": "alice-demo-password"}
    with TestClient(example.app) as client:
        assert client.get("/me").status_code == 401
        issued = client.post("/token", json=credentials)
        assert issued.status_code == 200
        token = issued.json()["access_token"]
        assert token.count(".") == 2  # header.payload.signature
        auth = {"Authorization": f"Bearer {token}"}

        assert client.get("/me", headers=auth).json() == {"user": "alice"}
        assert client.post("/logout", headers=auth).status_code == 200
        # The session is gone, so the unexpired JWT no longer works.
        assert client.get("/me", headers=auth).status_code == 401


def test_session_api() -> None:
    example = load_example("session_api")
    credentials = {"username": "alice", "password": "alice-demo-password"}
    with TestClient(example.app) as client:
        assert client.get("/public").json() == {"message": "Hello, guest!"}
        assert client.get("/profile").status_code == 401
        wrong = {"username": "alice", "password": "nope"}
        assert client.post("/login", json=wrong).status_code == 401

        token = client.post("/login", json=credentials).json()["token"]
        # The token is only in the body: no cookie for an API client.
        assert not client.cookies.get("session")
        bearer = {"Authorization": f"Bearer {token}"}
        header = {"X-Session-Token": token}
        assert client.get("/profile", headers=bearer).json() == {
            "user_id": "alice",
            "username": "alice",
            "roles": ["user"],
        }
        assert client.get("/public", headers=header).json() == {
            "message": "Hello, alice!"
        }

        assert client.post("/logout", headers=bearer).status_code == 200
        assert client.get("/profile", headers=bearer).status_code == 401
        assert client.post("/logout", headers=bearer).status_code == 401


@pytest.mark.skipif(
    importlib.util.find_spec("jwt") is None,
    reason="session_jwt_claims needs the jwt extra (PyJWT)",
)
def test_session_jwt_claims() -> None:
    example = load_example("session_jwt_claims")
    credentials = {"username": "alice", "password": "alice-demo-password"}
    with TestClient(example.app) as client:
        wrong = {"username": "alice", "password": "nope"}
        assert client.post("/auth/login", json=wrong).status_code == 401
        issued = client.post("/auth/login", json=credentials)
        assert issued.status_code == 200
        token = issued.json()["token"]
        claims = example.jwt.decode(token, options={"verify_signature": False})
        assert claims["tenant_id"] == "acme-corp"
        assert claims["api_version"] == "v2"
        assert claims["iss"] == "acme-corp"
        auth = {"Authorization": f"Bearer {token}"}
        assert client.get("/api/profile", headers=auth).json() == {
            "user_id": "alice",
            "username": "alice",
        }

        # The same session, signed with the same key, for another tenant.
        other = example.MultiTenantJWTSerializer(example.config, tenant_id="other")
        foreign = other.to_string(example.serializer.from_string(token))
        with pytest.raises(ValueError, match="Invalid tenant_id"):
            example.serializer.from_string(foreign)
        foreign_auth = {"Authorization": f"Bearer {foreign}"}
        assert client.get("/api/profile", headers=foreign_auth).status_code == 401


def test_oauth_state() -> None:
    example = load_example("oauth_state")
    # https: the binding cookie is Secure.
    with TestClient(example.app, base_url="https://testserver") as client:
        started = client.get("/login", follow_redirects=False)
        assert started.status_code == 307
        location = urlsplit(started.headers["location"])
        assert location.netloc == "provider.example.com"
        state = parse_qs(location.query)["state"][0]
        nonce = client.cookies.get("oauth_binding")
        assert nonce

        # A different browser (another binding) is rejected, and that attempt
        # consumes the state too.
        with TestClient(example.app, base_url="https://testserver") as attacker:
            attacker.cookies.set("oauth_binding", "attackers-own-nonce")
            rejected = attacker.get(
                "/callback",
                params={"state": state, "code": "x"},
                follow_redirects=False,
            )
            assert rejected.status_code == 400
        assert (
            client.get(
                "/callback",
                params={"state": state, "code": "x"},
                follow_redirects=False,
            ).status_code
            == 400
        )


def test_oauth_state_is_consumed_once() -> None:
    example = load_example("oauth_state")
    with TestClient(example.app, base_url="https://testserver") as client:
        started = client.get("/login", follow_redirects=False)
        state = parse_qs(urlsplit(started.headers["location"]).query)["state"][0]
        nonce = client.cookies.get("oauth_binding")
        assert nonce
        params = {"state": state, "code": "x"}

        done = client.get("/callback", params=params, follow_redirects=False)
        assert done.status_code == 307
        assert done.headers["location"] == "/dashboard"
        assert "oauth_binding" not in client.cookies

        # Replaying the callback from the same browser fails: the state is gone.
        client.cookies.set("oauth_binding", nonce)
        again = client.get("/callback", params=params, follow_redirects=False)
        assert again.status_code == 400


def test_cache_lock_serialises_rebuilds() -> None:
    example = load_example("cache_lock")
    with TestClient(example.app) as client, ThreadPoolExecutor(3) as pool:
        results = list(
            pool.map(
                lambda _: client.post("/reports/sales/rebuild").status_code, range(3)
            )
        )
    assert results == [200, 200, 200]
    # Three concurrent requests, never more than one inside the lock.
    assert example.rebuilds["max_running"] == 1


def test_cache_lock_non_blocking_import_refuses_a_second_run() -> None:
    example = load_example("cache_lock")
    with TestClient(example.app) as client, ThreadPoolExecutor(2) as pool:
        statuses = sorted(
            pool.map(lambda _: client.post("/imports").status_code, range(2))
        )
        assert statuses == [200, 409]
        # Released afterwards.
        assert client.post("/imports").status_code == 200


def test_rate_limit() -> None:
    example = load_example("rate_limit")
    with TestClient(example.app) as client:
        for _ in range(example.LIMIT):
            assert client.get("/search", params={"q": "x"}).status_code == 200
        limited = client.get("/search", params={"q": "x"})
        assert limited.status_code == 429
        assert 1 <= int(limited.headers["retry-after"]) <= example.WINDOW


@pytest.mark.skipif(
    importlib.util.find_spec("redis") is None,
    reason="redis_backend needs the redis extra",
)
def test_redis_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    reason = redis_skip_reason()
    if reason is not None:
        pytest.skip(reason)
    monkeypatch.setenv("REDIS_HOST", REDIS_HOST)
    monkeypatch.setenv("REDIS_PORT", str(REDIS_PORT))
    monkeypatch.delenv("REDIS_PASSWORD", raising=False)
    monkeypatch.delenv("REDIS_DB", raising=False)
    example = load_example("redis_backend")
    with TestClient(example.app) as client:
        backend = BackendProxy.get()
        client.portal.call(backend.clear)  # type: ignore[union-attr]
        try:
            first = client.get("/hello/redis")
            assert first.json() == {"hello": "redis"}
            etag = first.headers["etag"]
            cached = client.get("/hello/redis", headers={"If-None-Match": etag})
            assert cached.status_code == 304
            assert client.post("/visits").json() == {"visits": 1}
            assert client.post("/visits").json() == {"visits": 2}
        finally:
            client.portal.call(backend.clear)  # type: ignore[union-attr]
    # The lifespan unregistered the backend on shutdown.
    with pytest.raises(BackendNotFoundError):
        BackendProxy.get()


@pytest.mark.skipif(
    importlib.util.find_spec("redis") is None,
    reason="session_redis needs the redis extra",
)
def test_session_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    reason = redis_skip_reason()
    if reason is not None:
        pytest.skip(reason)
    monkeypatch.setenv("REDIS_HOST", REDIS_HOST)
    monkeypatch.setenv("REDIS_PORT", str(REDIS_PORT))
    monkeypatch.delenv("REDIS_PASSWORD", raising=False)
    monkeypatch.delenv("REDIS_DB", raising=False)
    example = load_example("session_redis")
    credentials = {"username": "alice", "password": "alice-demo-password"}
    with TestClient(example.app) as client:
        portal = client.portal
        assert portal is not None
        # clear() removes only this app's namespace, not the whole server.
        portal.call(example.backend.clear)
        try:
            wrong = {"username": "alice", "password": "nope"}
            assert client.post("/api/auth/login", json=wrong).status_code == 401

            first = client.post("/api/auth/login", json=credentials).json()
            assert first["user"] == {"username": "alice", "roles": ["user"]}
            auth = {"Authorization": f"Bearer {first['token']}"}

            profile = client.get("/api/user/profile", headers=auth).json()
            assert profile["user_id"] == "user_alice"
            assert profile["email"] == "alice@example.com"

            # The flash message from the login is shown once.
            messages = client.get("/api/messages", headers=auth).json()["messages"]
            assert [m["message"] for m in messages] == ["Login successful!"]
            assert client.get("/api/messages", headers=auth).json() == {"messages": []}

            updated = client.post(
                "/api/user/update", params={"email": "a@example.org"}, headers=auth
            )
            assert updated.status_code == 200
            profile = client.get("/api/user/profile", headers=auth).json()
            assert profile["email"] == "a@example.org"

            # The session is bound to the client's IP address. (A second
            # TestClient would run on another event loop than the Redis pool.)
            with pytest.raises(SessionSecurityError):
                portal.call(
                    example.session_manager.get_session, first["token"], "203.0.113.9"
                )

            second = client.post("/api/auth/login", json=credentials).json()
            auth2 = {"X-Session-Token": second["token"]}
            assert client.post("/api/auth/logout", headers=auth2).status_code == 200
            assert client.get("/api/user/profile", headers=auth2).status_code == 401

            third = client.post("/api/auth/login", json=credentials).json()
            auth3 = {"Authorization": f"Bearer {third['token']}"}
            ended = client.post("/api/auth/logout-all", headers=auth3).json()
            assert ended == {"message": "Logged out from 2 devices"}
            assert client.get("/api/user/profile", headers=auth).status_code == 401
            assert client.get("/api/user/profile", headers=auth3).status_code == 401
        finally:
            portal.call(example.backend.clear)
