"""project auto_build and watched_sha

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-09-04 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd0e1f2a3b4c5'
down_revision: Union[str, Sequence[str], None] = 'c9d0e1f2a3b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('projects') as batch:
        # Off for every existing project: building on the box is opted into
        # per project, never switched on by an upgrade.
        batch.add_column(
            sa.Column('auto_build', sa.Boolean(), nullable=False, server_default='0')
        )
        # The branch head the watcher last saw. Null while auto_build is off.
        batch.add_column(sa.Column('watched_sha', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('projects') as batch:
        batch.drop_column('watched_sha')
        batch.drop_column('auto_build')
