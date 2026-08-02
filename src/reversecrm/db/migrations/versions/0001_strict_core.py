"""Create the strict first-slice persistence schema."""

from alembic import op

from reversecrm.db.schema import metadata

revision = "0001_strict_core"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    metadata.drop_all(bind=op.get_bind())
