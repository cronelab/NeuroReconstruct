"""secondary MRI scans (additional slice-viewer base layers)

Revision ID: b2f4c81d5e30
Revises: 7a75b7caf589
Create Date: 2026-09-05

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2f4c81d5e30'
down_revision: Union[str, None] = '7a75b7caf589'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'secondary_scans',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('reconstruction_id', sa.Integer(), nullable=True),
        sa.Column('label', sa.String(length=64), nullable=False),
        sa.Column('modality', sa.String(length=32), nullable=True),
        sa.Column('filename', sa.String(length=255), nullable=False),
        sa.Column('stored_path', sa.String(length=512), nullable=False),
        sa.Column('resampled_path', sa.String(length=512), nullable=True),
        sa.Column('status', sa.String(length=32), nullable=True),
        sa.Column('error', sa.String(length=512), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['reconstruction_id'], ['reconstructions.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('secondary_scans')
