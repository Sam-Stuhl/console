"""access paths table

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-08-27 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c9d0e1f2a3b4'
down_revision: Union[str, Sequence[str], None] = 'b8c9d0e1f2a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'access_paths',
        sa.Column('id', sa.Text(), nullable=False),
        # Null means the console's own hostname, which is not a project.
        sa.Column('project_id', sa.Text(), nullable=True),
        sa.Column('hostname', sa.Text(), nullable=False),
        sa.Column('path', sa.Text(), nullable=False),
        sa.Column('cf_app_id', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('project_id', 'path'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('access_paths')
