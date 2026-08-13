"""Connected application platforms (Railway / Vercel / Supabase).

A platform connection stores a credential and nothing else: the project inventory
is read live per request and never mirrored locally. That is the point — there is
no local copy to drift out of sync, and nothing can claim a project vanished
because one API call came back short. Contrast cloud_servers, which does mirror
and therefore needed origin/sync_state/adoption semantics (migration 082).

Idempotent: MigrationService._fix_missing_columns runs db.create_all() on boot
before Alembic, so a fresh database already has the table. Guard on the live
schema like previous migrations.

Revision ID: 083_platform_connections
Revises: 082_cloud_server_adoption
Create Date: 2026-08-13
"""
from alembic import op
import sqlalchemy as sa

revision = '083_platform_connections'
down_revision = '082_cloud_server_adoption'
branch_labels = None
depends_on = None

TABLE = 'platform_connections'


def upgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if TABLE in set(inspector.get_table_names()):
        return

    op.create_table(
        TABLE,
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(length=64), nullable=False),
        sa.Column('platform', sa.String(length=32), nullable=False),
        # Encrypted at rest via app.utils.crypto — never a plaintext column.
        sa.Column('api_token_encrypted', sa.Text(), nullable=True),
        # Vercel teamId / Railway workspace: needed on each call, not secret.
        sa.Column('scope_id', sa.String(length=128), nullable=True),
        # Whose account the credential resolved to at save time, so a swapped
        # token shows up as a changed account instead of silently pointing
        # somewhere else.
        sa.Column('account_label', sa.String(length=128), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('last_verified_at', sa.DateTime(), nullable=True),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_platform_connections_platform', TABLE, ['platform'])


def downgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if TABLE not in set(inspector.get_table_names()):
        return
    try:
        op.drop_index('ix_platform_connections_platform', table_name=TABLE)
    except Exception:
        pass
    op.drop_table(TABLE)
