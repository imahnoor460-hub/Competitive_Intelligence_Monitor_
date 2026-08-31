"""0023_check_run_surface_index

Revision ID: e7c4a91b6d38
Revises: d5b8c1e04f27
Create Date: 2026-08-31 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'e7c4a91b6d38'
down_revision: Union[str, Sequence[str], None] = 'd5b8c1e04f27'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # check_runs only had an index on its primary key. Every read of this
    # table filters or partitions by surface_id and then orders by started_at:
    # the workspace-wide latest-per-surface query (routers/check_runs.py), the
    # per-surface history endpoint, and _reclaim_stale_running_checks. A
    # composite index serves all three and lets the ROW_NUMBER() partition be
    # satisfied in index order rather than by sorting the whole table.
    op.create_index(
        'ix_check_runs_surface_id_started_at',
        'check_runs',
        ['surface_id', 'started_at'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_check_runs_surface_id_started_at', table_name='check_runs')
