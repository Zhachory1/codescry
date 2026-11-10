"""Shared input-coercion helpers used by the CLI and HTTP API layers."""

from __future__ import annotations

from typing import Any


def coerce_positive_int(value: Any, *, name: str = "value") -> int:
    """Parse ``value`` as a positive integer, raising ``ValueError`` otherwise.

    Shared by the argparse CLI (which wraps this in ``ArgumentTypeError``) and
    the HTTP API (which wraps this in a 400 JSON error) so both entry points
    agree on what "positive integer" means instead of each defining its own
    near-identical check.
    """
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a positive integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if parsed <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return parsed
