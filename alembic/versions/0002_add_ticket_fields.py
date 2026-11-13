"""add ticket_price / ticket_note to document_chunks

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-23
"""
import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("document_chunks", sa.Column("ticket_price", sa.Numeric(10, 2), nullable=True))
    op.add_column("document_chunks", sa.Column("ticket_note", sa.String(length=256), nullable=True))


def downgrade() -> None:
    op.drop_column("document_chunks", "ticket_note")
    op.drop_column("document_chunks", "ticket_price")