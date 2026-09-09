"""Merge the recovered purchase migration history.

Revision ID: b8f9c2d14a6e
Revises: d5a73b7cb06a, f84f1f45443f
Create Date: 2026-08-19 00:00:00.000000

"""

from collections.abc import Sequence

revision: str = "b8f9c2d14a6e"
down_revision: str | Sequence[str] | None = ("d5a73b7cb06a", "f84f1f45443f")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Merge the recovered and previously visible revision branches."""


def downgrade() -> None:
    """Split the recovered and previously visible revision branches."""
