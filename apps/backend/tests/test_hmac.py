"""Unit tests for :mod:`app.security.hmac`."""

from __future__ import annotations

import base64

import pytest

from app.security.hmac import (
    canonical_payload,
    compute_signature,
    verify_signature,
)

# --------------------------------------------------------------------------
# canonical_payload
# --------------------------------------------------------------------------


def test_canonical_payload_fixed_order() -> None:
    """The canonical string concatenates in the locked order
    regardless of dict insertion order."""
    payload = canonical_payload(
        {
            "x-bu-id": "42",
            "x-user-name": "Alice",
            "x-user-email": "alice@example.com",
            "x-user-oid": "00000000-0000-0000-0000-000000000001",
        }
    )
    assert payload == (
        b"00000000-0000-0000-0000-000000000001\n"
        b"alice@example.com\n"
        b"Alice\n"
        b"42"
    )


def test_canonical_payload_case_insensitive_keys() -> None:
    """Header lookup is case-insensitive — APIM may emit
    ``X-User-Oid`` or ``x-user-oid`` depending on policy version."""
    payload = canonical_payload(
        {
            "X-User-Oid": "oid",
            "X-User-Email": "e@x.com",
            "X-User-Name": "n",
            "X-BU-ID": "7",
        }
    )
    assert payload == b"oid\ne@x.com\nn\n7"


def test_canonical_payload_missing_header_raises_keyerror() -> None:
    """A missing required header is a programming bug; surfacing as
    KeyError makes the failure obvious in tests."""
    with pytest.raises(KeyError):
        canonical_payload(
            {
                "x-user-oid": "oid",
                "x-user-email": "e@x.com",
                # x-user-name missing
                "x-bu-id": "1",
            }
        )


# --------------------------------------------------------------------------
# compute_signature
# --------------------------------------------------------------------------


def test_compute_signature_is_url_safe_base64_no_padding() -> None:
    """The signature must be safe to ship in an HTTP header without
    extra quoting: only ``A-Z a-z 0-9 - _`` and no ``=`` padding."""
    sig = compute_signature("secret", b"payload")
    assert "=" not in sig
    assert all(c.isalnum() or c in "-_" for c in sig)
    # 256-bit digest → 32 bytes → 43 char base64 (no padding).
    assert len(sig) == 43


def test_compute_signature_accepts_str_and_bytes_secret() -> None:
    """The helper accepts either ``str`` or ``bytes`` for the secret
    — APIM emits the secret as a string, but unit tests sometimes
    feed raw bytes."""
    sig_str = compute_signature("secret", b"x")
    sig_bytes = compute_signature(b"secret", b"x")
    assert sig_str == sig_bytes


def test_compute_signature_changes_with_payload() -> None:
    """Different payloads must produce different signatures —
    sanity check against accidental constant outputs."""
    a = compute_signature("k", b"alpha")
    b = compute_signature("k", b"beta")
    assert a != b


def test_compute_signature_matches_manual_hmac() -> None:
    """Cross-check against an independent HMAC-SHA256 implementation
    so a future refactor of the helper cannot silently change the
    wire format."""
    import hmac
    from hashlib import sha256

    expected = base64.urlsafe_b64encode(
        hmac.new(b"k", b"payload", sha256).digest()
    ).rstrip(b"=").decode("ascii")
    assert compute_signature("k", b"payload") == expected


# --------------------------------------------------------------------------
# verify_signature
# --------------------------------------------------------------------------


def test_verify_signature_accepts_valid() -> None:
    sig = compute_signature("secret", b"payload")
    assert verify_signature("secret", b"payload", sig) is True


def test_verify_signature_rejects_wrong_signature() -> None:
    """A tampered signature must not validate, even if only one char
    differs (the helper uses constant-time compare under the hood)."""
    sig = compute_signature("secret", b"payload")
    # Flip the first character to a different valid base64 char.
    tampered = ("a" if sig[0] != "a" else "b") + sig[1:]
    assert verify_signature("secret", b"payload", tampered) is False


def test_verify_signature_rejects_wrong_secret() -> None:
    sig = compute_signature("secret-a", b"payload")
    assert verify_signature("secret-b", b"payload", sig) is False


def test_verify_signature_rejects_wrong_payload() -> None:
    """Even with the right secret, a forged payload must fail."""
    sig = compute_signature("secret", b"payload-1")
    assert verify_signature("secret", b"payload-2", sig) is False


def test_verify_signature_handles_completely_invalid_string() -> None:
    """The helper must not raise on garbage signatures (a stray
    exception would leak debug info through 500 responses)."""
    assert verify_signature("secret", b"payload", "not-a-real-sig") is False
