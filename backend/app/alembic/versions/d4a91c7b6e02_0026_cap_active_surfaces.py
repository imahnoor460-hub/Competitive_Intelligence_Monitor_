"""0026_cap_active_surfaces

Deactivates surfaces past the per-competitor watch cap.

Discovery was generous long before anything limited what got *watched*: one
workspace had accumulated 282 active surfaces across eight competitors, each
one a daily scheduled check and a slot in every "Run check now" sweep. On a
0.2-vCPU container that is a sweep measured in tens of minutes, which is what
made Run Check look permanently stuck.

Nothing is deleted. The rows stay, `is_active` goes false, and a user can turn
any of them back on by hand — "we found this page and chose not to watch it"
is worth keeping.

The ranking below is a frozen copy of services/surface_selection.py as of this
revision, deliberately: a migration has to keep doing the same thing years
later, whatever that module goes on to become.

Revision ID: d4a91c7b6e02
Revises: c3f7d2b81a45
Create Date: 2026-09-02 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4a91c7b6e02'
down_revision: Union[str, Sequence[str], None] = 'c3f7d2b81a45'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Mirrors settings.max_active_surfaces_per_competitor's default. Frozen rather
# than imported: a deployer raising the setting later should not change what
# this historical migration did.
_CAP = 10

_TYPE_PRIORITY = {
    'pricing': 0,
    'product': 1,
    'changelog': 2,
    'blog': 3,
    'jobs': 4,
    'other': 5,
}


def _is_homepage(url: str) -> bool:
    """True for an origin with no path — "https://x.com" or "https://x.com/".

    Deliberately string-level rather than urlsplit: the value stored may be
    anything a user typed, and this only needs to be right about the common
    shape it is asked about.
    """

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
    """Deliberately a no-op.

    Reactivating everything would not restore the previous state — it would
    also switch on surfaces a user had deactivated by hand, and re-arm daily
    checks for pages nobody wants watched. The rows themselves were never
    touched, so nothing was lost to restore.
    """
