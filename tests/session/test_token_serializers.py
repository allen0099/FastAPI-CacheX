import warnings
from datetime import datetime
from datetime import timezone
from typing import cast

import pytest
from pydantic import SecretStr

from fastapi_cachex.backends import MemoryBackend
from fastapi_cachex.session.config import SessionConfig
from fastapi_cachex.session.manager import SessionManager
from fastapi_cachex.session.models import SessionToken
from fastapi_cachex.session.token_serializers import JWTTokenSerializer
from fastapi_cachex.session.token_serializers import SimpleTokenSerializer


class StubJWTModule:
    def __init__(self) -> None:
        self.encode_calls = 0
        self.decode_calls = 0
        self.last_encode_payload: dict[str, object] | None = None
        self.last_encode_kwargs: dict[str, object] | None = None
        self.last_decode_kwargs: dict[str, object] | None = None

    def encode(self, payload: dict[str, object], key: str, algorithm: str) -> str:
        self.encode_calls += 1
        self.last_encode_payload = payload
        self.last_encode_kwargs = {"key": key, "algorithm": algorithm}
        return f"encoded-{payload['sid']}"

    def decode(self, token_str: str, **kwargs: object) -> dict[str, object]:
        self.decode_calls += 1
        self.last_decode_kwargs = kwargs
        return self.last_encode_payload or {}


def test_simple_token_serializer_roundtrip() -> None:
    serializer = SimpleTokenSerializer()
    token = SessionToken(
        session_id="sid",
        signature="sig",
        issued_at=datetime.now(timezone.utc),
    )

    token_str = serializer.to_string(token)
    parsed = serializer.from_string(token_str)

    assert parsed.session_id == token.session_id
    assert parsed.signature == token.signature


def test_jwt_serializer_to_and_from_string_success() -> None:
    stub = StubJWTModule()
    config = SessionConfig(
        secret_key=SecretStr("b" * 32),
        token_format="jwt",
        session_ttl=120,
        jwt_issuer="issuer",
        jwt_audience="aud",
    )
    serializer = JWTTokenSerializer(config, jwt_module=stub)

    token = SessionToken(
        session_id="jwt-id",
        signature="",
        issued_at=datetime.now(timezone.utc),
    )

    token_str = serializer.to_string(token)

    assert stub.encode_calls == 1
    assert stub.last_encode_payload is not None
    payload = stub.last_encode_payload
    assert payload["sid"] == "jwt-id"
    assert payload["iss"] == "issuer"
    assert payload["aud"] == "aud"
    iat = cast("int", payload["iat"])
    exp = cast("int", payload["exp"])
    assert exp == iat + 120

    parsed = serializer.from_string(token_str)

    assert parsed.session_id == "jwt-id"
    assert parsed.signature == ""
    assert stub.decode_calls == 1
    assert stub.last_decode_kwargs is not None
    assert stub.last_decode_kwargs.get("issuer") == "issuer"
    assert stub.last_decode_kwargs.get("audience") == "aud"


def test_jwt_serializer_decode_failure() -> None:
    class FailingJWTModule:
        def encode(self, payload: dict[str, object], key: str, algorithm: str) -> str:
            return "token"

        def decode(self, token_str: str, **kwargs: object) -> dict[str, object]:
            msg = "boom"
            raise RuntimeError(msg)

    config = SessionConfig(secret_key=SecretStr("c" * 32), token_format="jwt")
    serializer = JWTTokenSerializer(config, jwt_module=FailingJWTModule())

    with pytest.raises(ValueError, match="Invalid JWT token"):
        serializer.from_string("token")


def test_jwt_serializer_invalid_payload() -> None:
    class InvalidPayloadJWTModule:
        def encode(self, payload: dict[str, object], key: str, algorithm: str) -> str:
            return "token"

        def decode(self, token_str: str, **kwargs: object) -> dict[str, object]:
            return {"iat": "bad", "exp": 0}

    config = SessionConfig(secret_key=SecretStr("d" * 32), token_format="jwt")
    serializer = JWTTokenSerializer(config, jwt_module=InvalidPayloadJWTModule())

    with pytest.raises(ValueError, match="Invalid JWT payload"):
        serializer.from_string("token")


def test_simple_token_overflow_timestamp() -> None:
    """A token with an astronomically large timestamp must raise ValueError (not OverflowError)."""
    serializer = SimpleTokenSerializer()
    # Construct a raw token string with an overflow-inducing timestamp
    huge_timestamp = "9" * 20
    token_str = f"some-session-id.some-signature.{huge_timestamp}"

    with pytest.raises(ValueError, match="Invalid timestamp in token"):
        serializer.from_string(token_str)


def test_jwt_serializer_without_issuer_and_audience() -> None:
    """JWTTokenSerializer must work without jwt_issuer/jwt_audience configured."""
    stub = StubJWTModule()
    config = SessionConfig(
        secret_key=SecretStr("e" * 32),
        token_format="jwt",
        session_ttl=60,
        jwt_issuer=None,
        jwt_audience=None,
    )
    serializer = JWTTokenSerializer(config, jwt_module=stub)

    token = SessionToken(
        session_id="no-iss-aud",
        signature="",
        issued_at=datetime.now(timezone.utc),
    )
    token_str = serializer.to_string(token)

    # Payload must not include iss/aud when they are None
    payload = stub.last_encode_payload or {}
    assert "iss" not in payload
    assert "aud" not in payload

    parsed = serializer.from_string(token_str)
    assert parsed.session_id == "no-iss-aud"

    # decode kwargs must not include issuer/audience
    decode_kwargs = stub.last_decode_kwargs or {}
    assert "issuer" not in decode_kwargs
    assert "audience" not in decode_kwargs


def test_jwt_serializer_non_string_sid() -> None:
    """If JWT decode returns a non-string sid, it must be coerced to str."""

    class IntSidJWTModule:
        def encode(self, payload: dict[str, object], key: str, algorithm: str) -> str:
            return "token"

        def decode(self, token_str: str, **kwargs: object) -> dict[str, object]:
            return {"sid": 42, "iat": 0, "exp": 9999999999}

    config = SessionConfig(secret_key=SecretStr("f" * 32), token_format="jwt")
    serializer = JWTTokenSerializer(config, jwt_module=IntSidJWTModule())

    parsed = serializer.from_string("token")
    assert parsed.session_id == "42"


def test_jwt_serializer_uses_an_injected_module() -> None:
    """`jwt_module` exists so PyJWT is not the only possible implementation."""
    stub = StubJWTModule()
    config = SessionConfig(secret_key=SecretStr("a" * 32), token_format="jwt")

    serializer = JWTTokenSerializer(config, jwt_module=stub)

    assert serializer.jwt_encoder is stub


def test_jwt_serializer_without_pyjwt_explains_the_extra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without the extra installed the error must name the extra."""
    import importlib

    real_import_module = importlib.import_module

    def fake_import_module(name: str, package: str | None = None) -> object:
        if name == "jwt":
            msg = "No module named 'jwt'"
            raise ImportError(msg)
        return real_import_module(name, package)

    monkeypatch.setattr(importlib, "import_module", fake_import_module)
    config = SessionConfig(secret_key=SecretStr("a" * 32), token_format="jwt")

    with pytest.raises(ImportError, match=r"fastapi-cachex\[jwt\]"):
        JWTTokenSerializer(config)


def test_jwt_serializer_rejects_a_non_numeric_iat() -> None:
    """`iat` arrives from the token, so a non-numeric value is external input."""

    class BadIatJWTModule:
        def encode(self, payload: dict[str, object], key: str, algorithm: str) -> str:
            return "token"

        def decode(self, token_str: str, **kwargs: object) -> dict[str, object]:
            return {"sid": "session-id", "iat": "not-a-number"}

    config = SessionConfig(secret_key=SecretStr("a" * 32), token_format="jwt")
    serializer = JWTTokenSerializer(config, jwt_module=BadIatJWTModule())

    with pytest.raises(ValueError, match="Invalid JWT payload"):
        serializer.from_string("token")


def test_jwt_serializer_accepts_a_numeric_string_iat() -> None:
    """A string that is a number is coerced rather than rejected."""

    class StringIatJWTModule:
        def encode(self, payload: dict[str, object], key: str, algorithm: str) -> str:
            return "token"

        def decode(self, token_str: str, **kwargs: object) -> dict[str, object]:
            return {"sid": "session-id", "iat": "1700000000"}

    config = SessionConfig(secret_key=SecretStr("a" * 32), token_format="jwt")
    serializer = JWTTokenSerializer(config, jwt_module=StringIatJWTModule())

    token = serializer.from_string("token")

    assert token.session_id == "session-id"
    assert int(token.issued_at.timestamp()) == 1700000000


@pytest.mark.parametrize(
    "algorithm",
    ["RS256", "RS512", "ES256", "ES384", "PS256", "EdDSA"],
)
def test_jwt_serializer_rejects_asymmetric_algorithms(algorithm: str) -> None:
    """The built-in serializer only has `secret_key`, so it cannot sign RS/ES/PS/EdDSA.

    The config accepted these, and the first `create_session()` then failed
    inside PyJWT. They must fail when the manager is built instead.
    """
    config = SessionConfig(
        secret_key=SecretStr("a" * 32),
        token_format="jwt",
        jwt_algorithm=algorithm,
    )

    with pytest.raises(ValueError, match="custom token_serializer"):
        JWTTokenSerializer(config, jwt_module=StubJWTModule())


def test_session_manager_fails_at_startup_for_asymmetric_jwt() -> None:
    """The error surfaces when `SessionManager` is built, not on first use."""
    config = SessionConfig(
        secret_key=SecretStr("a" * 32),
        token_format="jwt",
        jwt_algorithm="RS256",
    )

    with pytest.raises(ValueError, match="RS256"):
        SessionManager(MemoryBackend(), config)


def test_session_manager_accepts_asymmetric_jwt_with_custom_serializer() -> None:
    """A custom serializer that holds the key pair is still allowed."""
    config = SessionConfig(
        secret_key=SecretStr("a" * 32),
        token_format="jwt",
        jwt_algorithm="RS256",
    )
    serializer = SimpleTokenSerializer()

    manager = SessionManager(MemoryBackend(), config, token_serializer=serializer)

    assert manager._serializer is serializer


@pytest.mark.parametrize("algorithm", ["HS256", "HS384", "HS512"])
def test_jwt_serializer_round_trips_hmac_algorithms(algorithm: str) -> None:
    """The HMAC algorithms work end to end with real PyJWT."""
    pytest.importorskip("jwt")
    config = SessionConfig(
        secret_key=SecretStr("a" * 32),
        token_format="jwt",
        jwt_algorithm=algorithm,
    )
    serializer = JWTTokenSerializer(config)
    token = SessionToken(
        session_id="sid-1", signature="", issued_at=datetime.now(timezone.utc)
    )

    assert serializer.from_string(serializer.to_string(token)).session_id == "sid-1"


@pytest.mark.parametrize(
    ("algorithm", "secret", "warns"),
    [
        ("HS256", "a" * 32, False),
        ("HS384", "a" * 47, True),
        ("HS384", "a" * 48, False),
        ("HS512", "a" * 63, True),
        ("HS512", "a" * 64, False),
        # 32 characters but 64 UTF-8 bytes: the key length is counted in bytes.
        ("HS512", "é" * 32, False),
    ],
)
def test_jwt_serializer_warns_once_about_a_short_hmac_key(
    algorithm: str, secret: str, warns: bool
) -> None:
    """A secret shorter than the hash output warns when the serializer is built (#116)."""
    config = SessionConfig(
        secret_key=SecretStr(secret), token_format="jwt", jwt_algorithm=algorithm
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        JWTTokenSerializer(config, jwt_module=StubJWTModule())

    messages = [str(w.message) for w in caught if w.category is UserWarning]
    if warns:
        assert len(messages) == 1
        assert f"requires for {algorithm}" in messages[0]
        assert caught[0].filename == __file__
    else:
        assert messages == []
