from datetime import datetime
from datetime import timezone
from typing import cast

import pytest
from pydantic import SecretStr

from fastapi_cachex.session.config import SessionConfig
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
