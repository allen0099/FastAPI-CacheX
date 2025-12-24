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
