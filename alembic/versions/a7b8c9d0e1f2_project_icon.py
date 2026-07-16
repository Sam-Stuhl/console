"""project icon

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-07-16 09:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7b8c9d0e1f2'
down_revision: Union[str, Sequence[str], None] = 'f6a7b8c9d0e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('projects', sa.Column('icon_data', sa.LargeBinary(), nullable=True))
    op.add_column('projects', sa.Column('icon_content_type', sa.Text(), nullable=True))
    op.add_column('projects', sa.Column('icon_fetched_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('projects', 'icon_fetched_at')
    op.drop_column('projects', 'icon_content_type')
    op.drop_column('projects', 'icon_data')
