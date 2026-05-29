"""Security primitives for the MCP server.

Currently exposes the :mod:`sql_validator` module (issue #19), which
gates every SQL string the agent submits before it reaches the
``query.execute`` tool (#20) or :class:`SqlDatabaseClient`. Future
phase-2 work adds an HMAC request-signature verifier (#47) under this
same package.
"""

from __future__ import annotations

from .sql_validator import ValidationResult, validate

__all__ = ["ValidationResult", "validate"]
