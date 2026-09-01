"""0024_check_sweeps_and_queued_runs

Adds the "check all" parent row, links check runs to it, and introduces the
`queued` check-run state that the arq worker transitions out of.

Revision ID: b1e4c7a92f30
Revises: e7c4a91b6d38
Create Date: 2026-09-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b1e4c7a92f30'
down_revision: Union[str, Sequence[str], None] = 'e7c4a91b6d38'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'check_sweeps',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('workspace_id', sa.Integer(), nullable=False),
        sa.Column(
            'status',
            sa.Enum('queued', 'running', 'success', 'failed', name='checksweepstatus'),
            nullable=False,
        ),
        sa.Column('total', sa.Integer(), nullable=False),
        sa.Column('finished', sa.Integer(), nullable=False),
        sa.Column('failed_count', sa.Integer(), nullable=False),
        sa.Column('created_by_user_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_check_sweeps_id'), 'check_sweeps', ['id'], unique=False)
    op.create_index(
        op.f('ix_check_sweeps_workspace_id'), 'check_sweeps', ['workspace_id'], unique=False
    )

    op.add_column('check_runs', sa.Column('sweep_id', sa.Integer(), nullable=True))
    op.add_column('check_runs', sa.Column('enqueued_at', sa.DateTime(), nullable=True))
    op.add_column('check_runs', sa.Column('outcome', sa.String(), nullable=True))
    op.create_index(
        op.f('ix_check_runs_sweep_id'), 'check_runs', ['sweep_id'], unique=False
    )
    op.create_foreign_key(
        'fk_check_runs_sweep_id', 'check_runs', 'check_sweeps', ['sweep_id'], ['id']
    )

    # Existing rows keep their current status; nothing is backfilled. A run
    # only ever enters `queued` by being created there from now on.
    #
    # Same approach as 0015/0016 for extending a native Postgres enum. This is
    # a no-op on SQLite, where Enum is a VARCHAR + CHECK built from the model,
    # so the tests pick the new value up from the model definition alone.
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        op.execute("ALTER TYPE checkrunstatus ADD VALUE IF NOT EXISTS 'queued'")


def downgrade() -> None:
    op.drop_constraint('fk_check_runs_sweep_id', 'check_runs', type_='foreignkey')
    op.drop_index(op.f('ix_check_runs_sweep_id'), table_name='check_runs')
    op.drop_column('check_runs', 'outcome')
    op.drop_column('check_runs', 'enqueued_at')
    op.drop_column('check_runs', 'sweep_id')

    op.drop_index(op.f('ix_check_sweeps_workspace_id'), table_name='check_sweeps')
    op.drop_index(op.f('ix_check_sweeps_id'), table_name='check_sweeps')
    op.drop_table('check_sweeps')
    sa.Enum(name='checksweepstatus').drop(op.get_bind(), checkfirst=True)

    # The 'queued' value stays on checkrunstatus: Postgres has no counterpart
    # to ADD VALUE for removing one, the same pragmatic tradeoff 0015 and 0016
    # already took. Harmless — no row can hold it after this downgrade, since
    # the code that writes it is gone too.
