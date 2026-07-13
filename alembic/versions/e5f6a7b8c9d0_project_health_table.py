"""project_health table

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-07-12 05:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, Sequence[str], None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'project_health',
        sa.Column('project_id', sa.Text(), nullable=False),
        sa.Column('state', sa.Text(), nullable=False),
        sa.Column('fail_streak', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('alerted', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('checked_at', sa.DateTime(), nullable=True),
        sa.Column('changed_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('project_id'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('project_health')
