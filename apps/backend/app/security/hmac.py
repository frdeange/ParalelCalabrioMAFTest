"""HMAC signing / verification primitives shared with the APIM policy.

Background
----------
APIM signs every request it forwards to the backend (and to MCP) with
a shared secret pulled from Key Vault — this is the "defense in
depth" leg described in PLAN.md §2 (D3) and §7. Even if a Container
App endpoint is accidentally exposed, an attacker would need both
the secret and the exact APIM contract to forge a request.

Signing payload (locked contract)
---------------------------------
The signed string is the concatenation of the four identity headers
APIM injects, separated by ``\\n``:

::

    x-user-oid + "\\n" + x-user-email + "\\n" + x-user-name + "\\n" + x-bu-id

* The order is **fixed** — both sides must serialise the same way.
* The separator is ``\\n`` because it cannot appear inside any of the
  Entra-issued claims (oid is a GUID, email/name are RFC 5322).
* The body is **not** signed: the AG-UI endpoint receives an arbitrary
  JSON payload that the user controls, so signing it would either
  force APIM to buffer the request or open us to canonicalisation
  bugs. Identity-only signing matches the threat model — we want to
  prove the request came through APIM with the identity APIM
  asserts.

The signature is the URL-safe Base64 (no padding) of
``HMAC_SHA256(secret, payload)`` so it round-trips through HTTP
headers without quoting. APIM's ``hmac-sign`` policy fragment uses
the same algorithm (see ADR-0005 once it lands).

Why a dedicated module
----------------------
:func:`compute_signature` is exported so the future ``hmac-sign``
fragment can be unit-tested against the exact same bytes the backend
verifies. :func:`verify_signature` wraps :func:`hmac.compare_digest`
so callers cannot accidentally use ``==`` and leak the secret via
timing side-channels.
"""

from __future__ import annotations

import base64
import hmac
from hashlib import sha256

# Order is part of the wire contract. Do NOT shuffle. The constant is
# private (``_``) because callers should only ever build the payload
# via :func:`canonical_payload`, never by hand.
_SIGNED_HEADERS: tuple[str, ...] = (
    "x-user-oid",
    "x-user-email",
    "x-user-name",
    "x-bu-id",
)


def canonical_payload(headers: dict[str, str]) -> bytes:
    """Build the canonical signing string from a header dict.

    ``headers`` keys are case-insensitive — the function lower-cases
    them before lookup so both ``X-User-Oid`` and ``x-user-oid``
    yield the same payload. Missing keys raise :class:`KeyError`;
    callers are expected to validate header presence *before* signing
    so the failure mode is obvious in tests.
    """
    lowered = {k.lower(): v for k, v in headers.items()}
    parts = [lowered[name] for name in _SIGNED_HEADERS]
    return "\n".join(parts).encode("utf-8")


def compute_signature(secret: str | bytes, payload: bytes) -> str:
    """Return the URL-safe Base64 (no padding) HMAC-SHA256 signature.

    The signature is intentionally URL-safe so it round-trips through
    HTTP headers and query strings without further quoting. Padding
    is stripped because the length is implicit (256 bits → 43 chars).
    """
    secret_bytes = secret.encode("utf-8") if isinstance(secret, str) else secret
    digest = hmac.new(secret_bytes, payload, sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def verify_signature(
    secret: str | bytes,
    payload: bytes,
    provided_signature: str,
) -> bool:
    """Constant-time compare of the expected signature with the one
    sent by the caller.

    Uses :func:`hmac.compare_digest` to avoid leaking the secret via
    timing attacks (``==`` short-circuits on the first different
    byte). Returns ``False`` rather than raising so the caller can
    return a generic 401 without exposing which side mismatched.
    """
    expected = compute_signature(secret, payload)
    return hmac.compare_digest(expected, provided_signature)


__all__ = [
    "canonical_payload",
    "compute_signature",
    "verify_signature",
]
