"""project access fields

Revision ID: a1b2c3d4e5f6
Revises: 8c2906176da4
Create Date: 2026-07-10 22:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '8c2906176da4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'projects',
        sa.Column('protected', sa.Boolean(), nullable=False, server_default='0'),
    )
    op.add_column('projects', sa.Column('access_emails', sa.Text(), nullable=True))
    op.add_column('projects', sa.Column('cf_app_id', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('projects', 'cf_app_id')
    op.drop_column('projects', 'access_emails')
    op.drop_column('projects', 'protected')
