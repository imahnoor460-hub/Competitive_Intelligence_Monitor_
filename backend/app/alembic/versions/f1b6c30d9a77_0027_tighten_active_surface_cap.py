"""0027_tighten_active_surface_cap

Lowers the per-competitor watch cap from 10 to 5 — the homepage plus the four
highest-ranked pages — and deactivates whatever sits past it.

A second migration rather than an edit to `0026`: that revision is already on
`main` and may already have run somewhere, and a migration that has been
published has to keep meaning what it meant. Running both in sequence on a
fresh database is harmless — `0026` deactivates past 10, this one past 5.

Same rule as `0026`: nothing is deleted, and nothing is scheduled. Surfaces
past the cap keep their rows with `is_active = false`, which means no daily
check, no place in a "Run check now" sweep, and no other job touching them —
they exist so a user can switch one back on by hand.

The ranking below is again a frozen copy of services/surface_selection.py as
of this revision, for the same reason it was frozen in `0026`.

Revision ID: f1b6c30d9a77
Revises: d4a91c7b6e02
Create Date: 2026-09-02 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f1b6c30d9a77'
down_revision: Union[str, Sequence[str], None] = 'd4a91c7b6e02'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Mirrors settings.max_active_surfaces_per_competitor's default at this
# revision. Frozen rather than imported: a deployer changing the setting later
# should not change what this historical migration did.
_CAP = 5

_TYPE_PRIORITY = {
    'pricing': 0,
    'product': 1,
    'changelog': 2,
    'blog': 3,
    'jobs': 4,
    'other': 5,
}


def _is_homepage(url: str) -> bool:
    """True for an origin with no path — "https://x.com" or "https://x.com/"."""

    without_scheme = (url or '').split('://', 1)[-1]
    return without_scheme.rstrip('/').find('/') == -1


def _rank(row) -> tuple:
    return (
        0 if _is_homepage(row.url) else 1,
        _TYPE_PRIORITY.get(str(row.surface_type), len(_TYPE_PRIORITY)),
        row.id,
    )


def upgrade() -> None:
    bind = op.get_bind()

    rows = bind.execute(
        sa.text(
            "SELECT id, competitor_id, surface_type, url FROM surfaces "
            "WHERE is_active = true"
        )
    ).fetchall()

    by_competitor: dict[int, list] = {}
    for row in rows:
        by_competitor.setdefault(row.competitor_id, []).append(row)

    to_deactivate: list[int] = []
    for competitor_surfaces in by_competitor.values():
        ordered = sorted(competitor_surfaces, key=_rank)
        to_deactivate.extend(row.id for row in ordered[_CAP:])

    if not to_deactivate:
        return

    # Chunked: some drivers cap how many bind parameters one statement may
    # carry, and this list is thousands of ids on a busy install.
    for start in range(0, len(to_deactivate), 500):
        chunk = to_deactivate[start:start + 500]
        bind.execute(
            sa.text(
                "UPDATE surfaces SET is_active = false WHERE id IN :ids"
            ).bindparams(sa.bindparam("ids", value=tuple(chunk), expanding=True))
        )


def downgrade() -> None:
    """Deliberately a no-op, for the same reason as 0026.

    Reactivating rows would not restore the previous state — it would also
    switch on surfaces a user had deactivated by hand, and re-arm daily checks
    for pages nobody wants watched. Nothing was deleted, so nothing is lost.
    """
