"""Let a cloud server be adopted from the provider, not only provisioned by us.

``cloud_servers`` only ever held servers ServerKit created, so the Cloud page was
empty on a panel whose provider account was full of running instances — there was
no way to represent "this exists at the provider and we imported it". Adoption
needs three things the table did not have:

- ``origin``          'provisioned' (we created it, we own its lifecycle) vs
                      'adopted' (it pre-existed; destroy must be refused, because
                      it would take out infrastructure we never provisioned)
- ``sync_state``      in_sync / drifted / missing_remote. `missing_remote` is
                      deliberately distinct from the 'destroyed' status: we did
                      not observe a destroy, so we must not claim one.
- ``last_synced_at``  when the provider last confirmed this row

Plus a UNIQUE (provider_id, external_id): discovery re-runs on every sync, and
without it each pass would insert a fresh duplicate of every remote instance.
Existing duplicates are folded before the constraint is added, keeping the
lowest id, so the migration cannot fail on live data.

Idempotent: MigrationService._fix_missing_columns runs db.create_all() on boot
before Alembic, so a fresh database already matches the model. Guard on the live
schema like previous migrations.

Revision ID: 082_cloud_server_adoption
Revises: 081_monitors_first_class
Create Date: 2026-08-12
"""
from alembic import op
import sqlalchemy as sa

revision = '082_cloud_server_adoption'
down_revision = '081_monitors_first_class'
branch_labels = None
depends_on = None

TABLE = 'cloud_servers'
UQ_NAME = 'uq_cloud_servers_provider_external'

# Batch mode rebuilds the table on SQLite and needs every reflected FK to carry a
# name; cloud_servers' FKs are unnamed, so supply the convention (same reason as
# migration 081).
NAMING_CONVENTION = {
    'fk': 'fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s',
    'pk': 'pk_%(table_name)s',
    'uq': 'uq_%(table_name)s_%(column_0_name)s',
}

NEW_COLUMNS = [
    ('origin', sa.String(length=16),
     {'nullable': False, 'server_default': 'provisioned'}),
    ('sync_state', sa.String(length=16), {'nullable': True}),
    ('last_synced_at', sa.DateTime(), {'nullable': True}),
]


def _columns(inspector, table):
    if table not in set(inspector.get_table_names()):
        return {}
    return {c['name']: c for c in inspector.get_columns(table)}


def _constraint_names(inspector, table):
    try:
        return {c.get('name') for c in inspector.get_unique_constraints(table)}
    except Exception:
        return set()


def _dedupe_provider_external(conn):
    """Collapse pre-existing duplicate (provider_id, external_id) rows.

    Nothing wrote duplicates deliberately, but the column pair was unconstrained,
    so a retried create could have left two rows for one remote server. Keep the
    lowest id — the original — so adding the constraint cannot fail.
    """
    # Keeping MIN(id) per (provider_id, external_id) group is sufficient on its
    # own: a group with a single row IS its own minimum, so unique rows are never
    # matched. Avoids row-value `(a,b) IN (...)`, which older SQLite lacks.
    conn.execute(sa.text(f"""
        DELETE FROM {TABLE}
         WHERE external_id IS NOT NULL
           AND external_id <> ''
           AND id NOT IN (
               SELECT MIN(id) FROM {TABLE}
                WHERE external_id IS NOT NULL AND external_id <> ''
                GROUP BY provider_id, external_id
           )
    """))


def upgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if TABLE not in set(inspector.get_table_names()):
        return

    existing = _columns(inspector, TABLE)
    missing = [(n, t, kw) for n, t, kw in NEW_COLUMNS if n not in existing]
    if missing:
        with op.batch_alter_table(TABLE, naming_convention=NAMING_CONVENTION) as batch_op:
            for name, col_type, kwargs in missing:
                batch_op.add_column(sa.Column(name, col_type, **kwargs))

    # Backfill: every row that existed before adoption was, by definition, one we
    # provisioned ourselves.
    conn.execute(sa.text(
        f"UPDATE {TABLE} SET origin = 'provisioned' "
        f"WHERE origin IS NULL OR origin = ''"
    ))

    if UQ_NAME not in _constraint_names(inspector, TABLE):
        _dedupe_provider_external(conn)
        with op.batch_alter_table(TABLE, naming_convention=NAMING_CONVENTION) as batch_op:
            batch_op.create_unique_constraint(
                UQ_NAME, ['provider_id', 'external_id'])


def downgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if TABLE not in set(inspector.get_table_names()):
        return

    if UQ_NAME in _constraint_names(inspector, TABLE):
        with op.batch_alter_table(TABLE, naming_convention=NAMING_CONVENTION) as batch_op:
            batch_op.drop_constraint(UQ_NAME, type_='unique')

    existing = _columns(inspector, TABLE)
    with op.batch_alter_table(TABLE, naming_convention=NAMING_CONVENTION) as batch_op:
        for name, _t, _kw in reversed(NEW_COLUMNS):
            if name in existing:
                batch_op.drop_column(name)
