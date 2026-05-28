"""Tests for :func:`app.deps.identity.get_caller`.

These tests build a single-route FastAPI app that mirrors what the
production AG-UI endpoint does — declare ``get_caller`` as a
dependency — so the assertions exercise the dependency as it will be
used in real traffic.
"""

from __future__ import annotations

from typing import Annotated, Any

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from app.deps.identity import (
    HEADER_BU_ID,
    HEADER_EMAIL,
    HEADER_NAME,
    HEADER_OID,
    HEADER_SIGNATURE,
    Caller,
    get_caller,
)
from app.security.hmac import canonical_payload, compute_signature
from app.settings import get_settings

# --------------------------------------------------------------------------
# Test fixtures
# --------------------------------------------------------------------------


@pytest.fixture()
def app() -> FastAPI:
    """A minimal app exposing the dep behind ``GET /whoami``.

    We deliberately do NOT mount the AG-UI endpoint — this isolates
    the dep so a failure in the workflow wiring cannot mask an
    identity bug (and vice-versa).
    """
    app_ = FastAPI()

    @app_.get("/whoami")
    def whoami(caller: Annotated[Caller, Depends(get_caller)]) -> dict[str, Any]:
        return {
            "oid": caller.oid,
            "email": caller.email,
            "name": caller.name,
            "bu_id": caller.bu_id,
        }

    return app_


@pytest.fixture()
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def _signed_headers(
    *,
    oid: str = "00000000-0000-0000-0000-000000000001",
    email: str = "alice@example.com",
    name: str = "Alice Example",
    bu_id: str = "42",
    secret: str | None = None,
) -> dict[str, str]:
    """Build the four identity headers + a valid signature."""
    if secret is None:
        secret = get_settings().hmac_shared_secret.get_secret_value()
    headers = {
        HEADER_OID: oid,
        HEADER_EMAIL: email,
        HEADER_NAME: name,
        HEADER_BU_ID: bu_id,
    }
    headers[HEADER_SIGNATURE] = compute_signature(secret, canonical_payload(headers))
    return headers


# --------------------------------------------------------------------------
# Happy path
# --------------------------------------------------------------------------


def test_valid_signature_returns_caller(client: TestClient) -> None:
    """A correctly signed request returns the parsed :class:`Caller`."""
    response = client.get("/whoami", headers=_signed_headers())
    assert response.status_code == 200
    assert response.json() == {
        "oid": "00000000-0000-0000-0000-000000000001",
        "email": "alice@example.com",
        "name": "Alice Example",
        "bu_id": 42,
    }


def test_bu_id_is_parsed_as_int(client: TestClient) -> None:
    """``x-bu-id`` arrives as a string but the Caller exposes an int.

    Downstream code (workflow + SQL filter) uses the int form to
    avoid every consumer having to parse it.
    """
    response = client.get("/whoami", headers=_signed_headers(bu_id="123"))
    assert response.status_code == 200
    assert response.json()["bu_id"] == 123


# --------------------------------------------------------------------------
# 400 path — bu_id is a client contract bug
# --------------------------------------------------------------------------


def test_missing_bu_id_returns_400(client: TestClient) -> None:
    """An APIM contract bug surfaces as 400 so the break is loud in
    the upstream pipeline, not silently treated as auth failure."""
    headers = _signed_headers()
    headers.pop(HEADER_BU_ID)
    response = client.get("/whoami", headers=headers)
    assert response.status_code == 400
    assert HEADER_BU_ID in response.json()["detail"]


def test_non_integer_bu_id_returns_400(client: TestClient) -> None:
    """``x-bu-id`` is contractually an integer — APIM resolves it
    from a JWT claim or a domain map, both numeric. A string body
    is a contract bug."""
    headers = _signed_headers()
    headers[HEADER_BU_ID] = "not-a-number"
    # The signature for this payload is now stale; we re-sign so the
    # test isolates the "non-integer" path specifically.
    headers[HEADER_SIGNATURE] = compute_signature(
        get_settings().hmac_shared_secret.get_secret_value(),
        canonical_payload(headers),
    )
    response = client.get("/whoami", headers=headers)
    assert response.status_code == 400


# --------------------------------------------------------------------------
# 401 path — auth failures (uniform error, no leg-specific detail)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "missing_header",
    [HEADER_OID, HEADER_EMAIL, HEADER_NAME, HEADER_SIGNATURE],
)
def test_missing_identity_header_returns_401(
    client: TestClient, missing_header: str
) -> None:
    """Missing identity OR signature → 401 with a generic detail.
    We do not differentiate the legs in the response body."""
    headers = _signed_headers()
    headers.pop(missing_header)
    response = client.get("/whoami", headers=headers)
    assert response.status_code == 401
    # Body must not name the specific missing header.
    detail = response.json()["detail"].lower()
    assert missing_header.lower() not in detail


def test_invalid_signature_returns_401(client: TestClient) -> None:
    """Tampering with any header invalidates the signature → 401."""
    headers = _signed_headers()
    # Tamper with bu_id without re-signing.
    headers[HEADER_BU_ID] = "999"
    response = client.get("/whoami", headers=headers)
    assert response.status_code == 401


def test_signature_with_wrong_secret_returns_401(client: TestClient) -> None:
    """A request signed with the wrong secret is rejected — the
    constant-time compare keeps timing-side-channels closed."""
    headers = _signed_headers(secret="attacker-secret")
    response = client.get("/whoami", headers=headers)
    assert response.status_code == 401


def test_empty_signature_returns_401(client: TestClient) -> None:
    """An empty signature header is treated as missing — APIM should
    never emit blank values."""
    headers = _signed_headers()
    headers[HEADER_SIGNATURE] = ""
    response = client.get("/whoami", headers=headers)
    assert response.status_code == 401


# --------------------------------------------------------------------------
# State propagation
# --------------------------------------------------------------------------


def test_caller_is_stashed_on_request_state() -> None:
    """``request.state.caller`` makes the parsed identity reachable
    from downstream code without re-running the dependency.

    We probe this with a custom route that reads ``request.state``
    directly — a regression here would mean telemetry enrichers and
    workflow executors lose their identity context.
    """
    app = FastAPI()

    @app.get("/probe")
    def probe(
        request: Request,
        _caller: Annotated[Caller, Depends(get_caller)],
    ) -> dict[str, Any]:
        stored = request.state.caller
        return {"oid_match": stored.oid, "type_name": type(stored).__name__}

    with TestClient(app) as client_:
        response = client_.get("/probe", headers=_signed_headers())

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["oid_match"] == "00000000-0000-0000-0000-000000000001"
    assert body["type_name"] == "Caller"
