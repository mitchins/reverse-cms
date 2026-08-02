"""Create the frozen strict first-slice persistence schema."""

import sqlalchemy as sa
from alembic import op

revision = "0001_strict_core"
down_revision = None
branch_labels = None
depends_on = None

_OBJECT_ID_TARGET = "object.id"
_DOCUMENT_OBJECT_ID_TARGET = "document.object_id"


def upgrade() -> None:
    """Create revision 0001 without importing mutable application metadata."""

    op.create_table(
        "audit_event",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("affected_id", sa.Text(), nullable=False),
        sa.Column("correlation_key", sa.Text(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("payload_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("action", "correlation_key"),
        sqlite_strict=True,
    )
    op.create_table(
        "evidence_blob",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("detected_mime", sa.Text(), nullable=False),
        sa.Column("relative_path", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.CheckConstraint("byte_size >= 0", name="ck_evidence_size"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("relative_path"),
        sa.UniqueConstraint("sha256"),
        sqlite_strict=True,
    )
    op.create_table(
        "object",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('organisation','property','account','asset','document')",
            name="ck_object_kind",
        ),
        sa.PrimaryKeyConstraint("id"),
        sqlite_strict=True,
    )
    op.create_table(
        "asset",
        sa.Column("object_id", sa.Text(), nullable=False),
        sa.Column("make", sa.Text()),
        sa.Column("model", sa.Text()),
        sa.Column("serial", sa.Text()),
        sa.Column("order_reference", sa.Text()),
        sa.ForeignKeyConstraint(["object_id"], [_OBJECT_ID_TARGET], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("object_id"),
        sqlite_strict=True,
    )
    op.create_index("ix_asset_model", "asset", ["model"])
    op.create_index(
        "uq_asset_order_reference",
        "asset",
        ["order_reference"],
        unique=True,
        sqlite_where=sa.text("order_reference IS NOT NULL"),
    )
    op.create_index(
        "uq_asset_serial",
        "asset",
        ["serial"],
        unique=True,
        sqlite_where=sa.text("serial IS NOT NULL"),
    )
    op.create_table(
        "document",
        sa.Column("object_id", sa.Text(), nullable=False),
        sa.Column("evidence_blob_id", sa.Text(), nullable=False),
        sa.Column("document_type", sa.Text()),
        sa.Column("document_date", sa.Text()),
        sa.Column("processing_state", sa.Text(), server_default="pending", nullable=False),
        sa.Column("extractor_version", sa.Text()),
        sa.CheckConstraint(
            "processing_state IN ('pending','processing','complete','failed')",
            name="ck_document_processing_state",
        ),
        sa.ForeignKeyConstraint(["evidence_blob_id"], ["evidence_blob.id"]),
        sa.ForeignKeyConstraint(["object_id"], [_OBJECT_ID_TARGET], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("object_id"),
        sa.UniqueConstraint("evidence_blob_id"),
        sqlite_strict=True,
    )
    op.create_table(
        "organisation",
        sa.Column("object_id", sa.Text(), nullable=False),
        sa.Column("canonical_name", sa.Text(), nullable=False),
        sa.Column("normalised_name", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["object_id"], [_OBJECT_ID_TARGET], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("object_id"),
        sqlite_strict=True,
    )
    op.create_table(
        "property",
        sa.Column("object_id", sa.Text(), nullable=False),
        sa.Column("normalised_address", sa.Text(), nullable=False),
        sa.Column("occupancy", sa.Text()),
        sa.CheckConstraint(
            "occupancy IS NULL OR occupancy IN ('rental','occupied')",
            name="ck_property_occupancy",
        ),
        sa.ForeignKeyConstraint(["object_id"], [_OBJECT_ID_TARGET], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("object_id"),
        sqlite_strict=True,
    )
    op.create_index("ix_property_address", "property", ["normalised_address"])
    op.create_table(
        "source_reference",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("source_kind", sa.Text(), nullable=False),
        sa.Column("source_identity", sa.Text(), nullable=False),
        sa.Column("evidence_blob_id", sa.Text(), nullable=False),
        sa.Column("original_name", sa.Text()),
        sa.Column("received_metadata", sa.Text(), server_default="{}", nullable=False),
        sa.Column("received_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["evidence_blob_id"], ["evidence_blob.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_kind", "source_identity"),
        sqlite_strict=True,
    )
    op.create_table(
        "account",
        sa.Column("object_id", sa.Text(), nullable=False),
        sa.Column("account_type", sa.Text(), nullable=False),
        sa.Column("suffix", sa.Text(), nullable=False),
        sa.Column("issuer_organisation_id", sa.Text(), nullable=False),
        sa.Column("related_subject_id", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["issuer_organisation_id"], ["organisation.object_id"]),
        sa.ForeignKeyConstraint(["object_id"], [_OBJECT_ID_TARGET], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["related_subject_id"], [_OBJECT_ID_TARGET]),
        sa.PrimaryKeyConstraint("object_id"),
        sa.UniqueConstraint("issuer_organisation_id", "account_type", "suffix"),
        sqlite_strict=True,
    )
    op.create_index("ix_account_suffix", "account", ["suffix"])
    op.create_table(
        "extraction_signal",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("document_id", sa.Text(), nullable=False),
        sa.Column("signal_type", sa.Text(), nullable=False),
        sa.Column("normalised_value", sa.Text(), nullable=False),
        sa.Column("display_value", sa.Text(), nullable=False),
        sa.Column("source_locator", sa.Text(), nullable=False),
        sa.Column("extractor", sa.Text(), nullable=False),
        sa.Column("extractor_version", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "signal_type IN ('issuer_name','property_address','account_suffix','merchant_name',"
            "'purchase_date','amount_minor','make','model','serial','order_reference')",
            name="ck_extraction_signal_type",
        ),
        sa.ForeignKeyConstraint(["document_id"], [_DOCUMENT_OBJECT_ID_TARGET], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "signal_type", "normalised_value", "source_locator"),
        sqlite_strict=True,
    )
    op.create_index("ix_signal_lookup", "extraction_signal", ["signal_type", "normalised_value"])
    op.create_table(
        "organisation_alias",
        sa.Column("organisation_id", sa.Text(), nullable=False),
        sa.Column("normalised_alias", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organisation_id"], ["organisation.object_id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("organisation_id", "normalised_alias"),
        sqlite_strict=True,
    )
    op.create_table(
        "processing_job",
        sa.Column("document_id", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("lease_owner", sa.Text()),
        sa.Column("lease_expires_at", sa.Text()),
        sa.Column("error_code", sa.Text()),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "state IN ('pending','processing','complete','failed')", name="ck_job_state"
        ),
        sa.ForeignKeyConstraint(["document_id"], [_DOCUMENT_OBJECT_ID_TARGET], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("document_id"),
        sqlite_strict=True,
    )
    op.create_table(
        "proposal",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("document_id", sa.Text(), nullable=False),
        sa.Column("candidate_object_id", sa.Text(), nullable=False),
        sa.Column("predicate", sa.Text(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("state", sa.Text(), server_default="pending", nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "predicate IN ('concerns_property','purchase_evidence_for')",
            name="ck_proposal_predicate",
        ),
        sa.CheckConstraint("state IN ('pending','accepted','rejected')", name="ck_proposal_state"),
        sa.CheckConstraint("rank BETWEEN 1 AND 3", name="ck_proposal_rank"),
        sa.ForeignKeyConstraint(["candidate_object_id"], [_OBJECT_ID_TARGET]),
        sa.ForeignKeyConstraint(["document_id"], [_DOCUMENT_OBJECT_ID_TARGET], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "candidate_object_id", "predicate"),
        sa.UniqueConstraint("document_id", "rank"),
        sqlite_strict=True,
    )
    op.create_index("ix_proposal_document", "proposal", ["document_id"])
    op.create_table(
        "proposal_evidence",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("proposal_id", sa.Text(), nullable=False),
        sa.Column("evidence_code", sa.Text(), nullable=False),
        sa.Column("extraction_signal_id", sa.Text(), nullable=False),
        sa.Column("matched_object_id", sa.Text(), nullable=False),
        sa.Column("validation_version", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "evidence_code IN ('issuer_exact','address_exact','account_suffix_exact',"
            "'merchant_alias_exact','model_exact','serial_exact','order_reference_exact')",
            name="ck_proposal_evidence_code",
        ),
        sa.ForeignKeyConstraint(["extraction_signal_id"], ["extraction_signal.id"]),
        sa.ForeignKeyConstraint(["matched_object_id"], [_OBJECT_ID_TARGET]),
        sa.ForeignKeyConstraint(["proposal_id"], ["proposal.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "proposal_id", "evidence_code", "extraction_signal_id", "matched_object_id"
        ),
        sqlite_strict=True,
    )
    op.create_table(
        "review_decision",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("proposal_id", sa.Text(), nullable=False),
        sa.Column("reviewer", sa.Text(), nullable=False),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("decided_at", sa.Text(), nullable=False),
        sa.CheckConstraint("decision IN ('accepted','rejected')", name="ck_review_decision"),
        sa.ForeignKeyConstraint(["proposal_id"], ["proposal.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
        sqlite_strict=True,
    )
    op.create_index(
        "uq_review_accepted",
        "review_decision",
        ["proposal_id"],
        unique=True,
        sqlite_where=sa.text("decision = 'accepted'"),
    )
    op.create_table(
        "relation",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("document_id", sa.Text(), nullable=False),
        sa.Column("predicate", sa.Text(), nullable=False),
        sa.Column("target_object_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("confirmation_decision_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "predicate IN ('concerns_property','purchase_evidence_for')",
            name="ck_relation_predicate",
        ),
        sa.CheckConstraint("status = 'confirmed'", name="ck_relation_status"),
        sa.ForeignKeyConstraint(["confirmation_decision_id"], ["review_decision.id"]),
        sa.ForeignKeyConstraint(["document_id"], [_DOCUMENT_OBJECT_ID_TARGET]),
        sa.ForeignKeyConstraint(["target_object_id"], [_OBJECT_ID_TARGET]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "predicate", "target_object_id"),
        sqlite_strict=True,
    )
    op.create_index("ix_relation_target", "relation", ["target_object_id"])
    op.create_table(
        "evidence_link",
        sa.Column("relation_id", sa.Text(), nullable=False),
        sa.Column("proposal_evidence_id", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["proposal_evidence_id"], ["proposal_evidence.id"]),
        sa.ForeignKeyConstraint(["relation_id"], ["relation.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("relation_id", "proposal_evidence_id"),
        sqlite_strict=True,
    )


def downgrade() -> None:
    """Drop revision 0001 in reverse dependency order."""

    op.drop_table("evidence_link")
    op.drop_index("ix_relation_target", table_name="relation")
    op.drop_table("relation")
    op.drop_index("uq_review_accepted", table_name="review_decision")
    op.drop_table("review_decision")
    op.drop_table("proposal_evidence")
    op.drop_index("ix_proposal_document", table_name="proposal")
    op.drop_table("proposal")
    op.drop_table("processing_job")
    op.drop_table("organisation_alias")
    op.drop_index("ix_signal_lookup", table_name="extraction_signal")
    op.drop_table("extraction_signal")
    op.drop_index("ix_account_suffix", table_name="account")
    op.drop_table("account")
    op.drop_table("source_reference")
    op.drop_index("ix_property_address", table_name="property")
    op.drop_table("property")
    op.drop_table("organisation")
    op.drop_table("document")
    op.drop_index("uq_asset_serial", table_name="asset")
    op.drop_index("uq_asset_order_reference", table_name="asset")
    op.drop_index("ix_asset_model", table_name="asset")
    op.drop_table("asset")
    op.drop_table("object")
    op.drop_table("evidence_blob")
    op.drop_table("audit_event")
