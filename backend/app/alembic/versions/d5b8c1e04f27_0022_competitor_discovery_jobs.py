"""0022_competitor_discovery_jobs

Revision ID: d5b8c1e04f27
Revises: a7e3f6d4c9b2
Create Date: 2026-08-31 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd5b8c1e04f27'
down_revision: Union[str, Sequence[str], None] = 'a7e3f6d4c9b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'competitor_discovery_jobs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('workspace_id', sa.Integer(), nullable=False),
        sa.Column('competitor_id', sa.Integer(), nullable=False),
        sa.Column('website_url', sa.String(), nullable=False),
        sa.Column(
            'status',
            sa.Enum('queued', 'running', 'success', 'failed', name='competitordiscoveryjobstatus'),
            nullable=False,
        ),
        sa.Column('surfaces_discovered', sa.Integer(), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_by_user_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['competitor_id'], ['competitors.id'], ),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_competitor_discovery_jobs_id'), 'competitor_discovery_jobs', ['id'], unique=False
    )


def downgrade() -> None:
    op.drop_index(
        op.f('ix_competitor_discovery_jobs_id'), table_name='competitor_discovery_jobs'
    )
    op.drop_table('competitor_discovery_jobs')
    sa.Enum(name='competitordiscoveryjobstatus').drop(op.get_bind(), checkfirst=True)
