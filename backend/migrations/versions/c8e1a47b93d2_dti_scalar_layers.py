"""DTI scalar layers: a separate registration reference

A derived diffusion map (FA, ADC) joins the viewer as an ordinary secondary scan
-- same greyscale pane as a T2 or FLAIR -- but it cannot be registered the same
way: it has little anatomy for mutual information to lock onto. reference_path
names the b=0 volume it was derived from, which is registered instead, with the
resulting transform applied to the map.

Nullable, so every existing secondary scan keeps its current behaviour
(registered directly) without a data migration.

Revision ID: c8e1a47b93d2
Revises: b2f4c81d5e30
Create Date: 2026-09-23

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c8e1a47b93d2'
down_revision: Union[str, None] = 'b2f4c81d5e30'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('secondary_scans', sa.Column('reference_path', sa.String(length=512), nullable=True))


def downgrade() -> None:
    op.drop_column('secondary_scans', 'reference_path')
