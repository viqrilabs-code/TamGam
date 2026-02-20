"""add books table and book_chunk embedding type

Revision ID: 20260218_0001
Revises: <previous_revision>
Create Date: 2026-02-18
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '20260218_0001'
down_revision = 'ae3c52967305'  # Replace with your latest revision ID
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create enum if not exists
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE embedding_content_type_enum AS ENUM (
                'transcript_chunk', 'note_section', 'community_post', 'book_chunk'
            );
        EXCEPTION WHEN duplicate_object THEN
            ALTER TYPE embedding_content_type_enum ADD VALUE IF NOT EXISTS 'book_chunk';
        END $$;
    """)

    op.execute("""
        DO $$ BEGIN
            CREATE TYPE book_embed_status_enum AS ENUM ('pending', 'processing', 'completed', 'failed');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)

    op.create_table(
        'books',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('title', sa.String(500), nullable=False),
        sa.Column('author', sa.String(255), nullable=True),
        sa.Column('subject', sa.String(100), nullable=True),
        sa.Column('description', sa.Text, nullable=True),
        sa.Column('filename', sa.String(500), nullable=False),
        sa.Column('gcs_path', sa.String(1000), nullable=True),
        sa.Column('file_size', sa.Integer, nullable=True),
        sa.Column('page_count', sa.Integer, nullable=True),
        sa.Column('embed_status', sa.Text, nullable=False, server_default='pending'),
        sa.Column('chunk_count', sa.Integer, nullable=True),
        sa.Column('embed_error', sa.Text, nullable=True),
        sa.Column('embedded_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('NOW()')),
    )
    op.create_index('ix_books_subject', 'books', ['subject'])
    op.create_index('ix_books_embed_status', 'books', ['embed_status'])

    op.add_column('content_embeddings',
        sa.Column('book_id', postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        'fk_content_embeddings_book_id',
        'content_embeddings', 'books',
        ['book_id'], ['id'],
        ondelete='CASCADE',
    )
    op.create_index('ix_content_embeddings_book_id', 'content_embeddings', ['book_id'])

def downgrade() -> None:
    op.drop_index('ix_content_embeddings_book_id', 'content_embeddings')
    op.drop_constraint('fk_content_embeddings_book_id', 'content_embeddings', type_='foreignkey')
    op.drop_column('content_embeddings', 'book_id')
    op.drop_index('ix_books_embed_status', 'books')
    op.drop_index('ix_books_subject', 'books')
    op.drop_table('books')
    # Note: PostgreSQL doesn't support removing enum values easily; drop/recreate if needed