"""Caller identity dependency for FastAPI routes.

This module embodies the locked auth contract from PLAN.md §7:

1. APIM validates the user's JWT and resolves the Business Unit.
2. APIM injects four identity headers (``x-user-oid``,
   ``x-user-email``, ``x-user-name``, ``x-bu-id``) and an
   ``x-apim-signature`` HMAC over them.
3. The backend never touches Entra ID directly — every route that
   needs caller context depends on :func:`get_caller`, which verifies
   the signature and returns a typed :class:`Caller`.

Failure modes
-------------
The dependency maps failure modes to HTTP status codes the way the
issue specifies (see #13):

* Missing ``x-bu-id`` → ``400 Bad Request``. Treated as a client
  contract bug — APIM should *always* set it. Surfacing 400 makes the
  break loud in the APIM pipeline.
* Missing identity header or missing/invalid signature → ``401
  Unauthorized``. We do **not** differentiate "missing header" from
  "wrong signature" in the response body — exposing which leg failed
  would help an attacker map the contract.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from app.security.hmac import canonical_payload, verify_signature
from app.settings import Settings, get_settings

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Caller value object
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Caller:
    """Identity APIM asserts about the request.

    Frozen + ``slots`` so it is cheap to hash and stash on
    ``request.state``. All four fields are required — by the time a
    request reaches the backend APIM is guaranteed to have set them
    (PLAN.md §7 BU resolution 4-layer ensures ``bu_id`` even in the
    absence of a JWT claim).
    """

    oid: str
    email: str
    name: str
    bu_id: int


# --------------------------------------------------------------------------
# Header names (single source of truth)
# --------------------------------------------------------------------------

# Lower-case because Starlette normalises header lookups via
# ``request.headers.get`` (case-insensitive); using the lower form
# keeps tests honest about what they are exercising.
HEADER_OID = "x-user-oid"
HEADER_EMAIL = "x-user-email"
HEADER_NAME = "x-user-name"
HEADER_BU_ID = "x-bu-id"
HEADER_SIGNATURE = "x-apim-signature"


# --------------------------------------------------------------------------
# Dependency
# --------------------------------------------------------------------------


def get_caller(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> Caller:
    """Return the verified :class:`Caller` for ``request``.

    Raises
    ------
    HTTPException(400)
        ``x-bu-id`` is missing or not an integer.
    HTTPException(401)
        Any identity header is missing, the signature is missing or
        the HMAC verification fails.
    """
    headers = request.headers

    # 1. x-bu-id — special-cased to 400 because it's an APIM-contract
    #    bug, not an auth failure.
    raw_bu_id = headers.get(HEADER_BU_ID)
    if raw_bu_id is None:
        logger.warning("identity: %s header missing", HEADER_BU_ID)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Missing {HEADER_BU_ID} header",
        )
    try:
        bu_id = int(raw_bu_id)
    except ValueError:
        logger.warning("identity: %s header not an int: %r", HEADER_BU_ID, raw_bu_id)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{HEADER_BU_ID} must be an integer",
        ) from None

    # 2. Identity headers + signature. Each of these failures collapses
    #    into the same 401 — we do not leak which leg broke.
    oid = headers.get(HEADER_OID)
    email = headers.get(HEADER_EMAIL)
    name = headers.get(HEADER_NAME)
    signature = headers.get(HEADER_SIGNATURE)
    if not (oid and email and name and signature):
        # ``not <header>`` covers ``None`` and empty strings — APIM
        # should never inject blank user fields and we treat them as
        # tampered.
        logger.warning(
            "identity: missing header — oid=%s email=%s name=%s sig=%s",
            bool(oid),
            bool(email),
            bool(name),
            bool(signature),
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid identity",
        )

    # 3. HMAC verification — constant-time inside the helper.
    payload = canonical_payload(
        {
            HEADER_OID: oid,
            HEADER_EMAIL: email,
            HEADER_NAME: name,
            HEADER_BU_ID: raw_bu_id,
        }
    )
    secret = settings.hmac_shared_secret.get_secret_value()
    if not verify_signature(secret, payload, signature):
        logger.warning("identity: HMAC signature mismatch for oid=%s", oid)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid identity",
        )

    caller = Caller(oid=oid, email=email, name=name, bu_id=bu_id)
    # Stash on request.state so downstream code (workflow executors,
    # telemetry enrichment) can read it without re-running the dep.
    request.state.caller = caller
    return caller


CallerDep = Annotated[Caller, Depends(get_caller)]
"""Convenience alias so routes can declare ``caller: CallerDep``."""


__all__ = [
    "Caller",
    "CallerDep",
    "HEADER_BU_ID",
    "HEADER_EMAIL",
    "HEADER_NAME",
    "HEADER_OID",
    "HEADER_SIGNATURE",
    "get_caller",
]
