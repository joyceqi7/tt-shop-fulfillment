"""Reconciliation window behavior."""

from __future__ import annotations

from app.services import reconciliation


def test_overlap_seconds_is_large_enough_for_late_visible_orders() -> None:
    assert reconciliation.OVERLAP_SECONDS >= 3600
