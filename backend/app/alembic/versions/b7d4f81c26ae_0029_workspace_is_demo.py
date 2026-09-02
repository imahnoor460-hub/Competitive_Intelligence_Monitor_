"""0029_workspace_is_demo

Adds the flag behind the public read-only demo workspace.

Additive and inert: one boolean column defaulting to false, so every existing
workspace keeps behaving exactly as it does today. Only a workspace explicitly
flagged by scripts/provision_demo.py is restricted, and nothing reachable over
HTTP can set or clear the flag.

Revision ID: b7d4f81c26ae
Revises: a83c5e6f2b91
Create Date: 2026-09-02 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7d4f81c26ae'
down_revision: Union[str, Sequence[str], None] = 'a83c5e6f2b91'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'workspaces',
        sa.Column(
            'is_demo',
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column('workspaces', 'is_demo')
