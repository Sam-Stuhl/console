"""project git_provider

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2026-10-14 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e1f2a3b4c5d6'
down_revision: Union[str, Sequence[str], None] = 'd0e1f2a3b4c5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('projects') as batch:
        # Every existing project keeps building from GitHub: the server
        # default fills the column for them, and nothing else is touched.
        batch.add_column(
            sa.Column('git_provider', sa.Text(), nullable=False, server_default='github')
        )


def downgrade() -> None:
    """Downgrade schema. Run this before pinning the console back to an image
    from before this revision: an older image does not know this revision and
    its startup migration refuses the database.

    Dropping the column makes every project a GitHub project again, so build
    on push is switched off first for any that were not: an older console
    would otherwise poll GitHub for that owner/repo, and could build a
    same-named GitHub repo into the app's image."""
    op.execute("UPDATE projects SET auto_build = 0 WHERE git_provider != 'github'")
    with op.batch_alter_table('projects') as batch:
        batch.drop_column('git_provider')
