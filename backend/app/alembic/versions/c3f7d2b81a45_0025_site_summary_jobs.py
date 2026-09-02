"""0025_site_summary_jobs

Adds the job row behind POST /site-summary/refresh, so the manual "Analyze
site" refresh enqueues and returns instead of holding the request open across
one HTTP fetch per active surface.

Revision ID: c3f7d2b81a45
Revises: b1e4c7a92f30
Create Date: 2026-09-02 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3f7d2b81a45'
down_revision: Union[str, Sequence[str], None] = 'b1e4c7a92f30'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'site_summary_jobs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('workspace_id', sa.Integer(), nullable=False),
        sa.Column('competitor_id', sa.Integer(), nullable=False),
        sa.Column(
            'status',
            sa.Enum(
                'queued', 'running', 'success', 'failed',
                name='sitesummaryjobstatus',
            ),
            nullable=False,
        ),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_by_user_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
        sa.ForeignKeyConstraint(['competitor_id'], ['competitors.id'], ),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_site_summary_jobs_id'), 'site_summary_jobs', ['id'], unique=False
    )
    op.create_index(
        op.f('ix_site_summary_jobs_competitor_id'),
        'site_summary_jobs',
        ['competitor_id'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f('ix_site_summary_jobs_competitor_id'), table_name='site_summary_jobs'
    )
    op.drop_index(op.f('ix_site_summary_jobs_id'), table_name='site_summary_jobs')
    op.drop_table('site_summary_jobs')
    # Postgres keeps the enum type after the table goes; drop it so the
    # downgrade/upgrade round-trip CI runs does not fail on a duplicate type.
    sa.Enum(name='sitesummaryjobstatus').drop(op.get_bind(), checkfirst=True)
