"""SQLAlchemy Core schema; deliberately small and SQLite-native."""

from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
)

metadata = MetaData()

object_table = Table(
    "object",
    metadata,
    Column("id", Text, primary_key=True),
    Column("kind", Text, nullable=False),
    Column("label", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    CheckConstraint(
        "kind IN ('organisation','property','account','asset','document')", name="ck_object_kind"
    ),
    sqlite_strict=True,
)

organisation = Table(
    "organisation",
    metadata,
    Column("object_id", Text, ForeignKey("object.id", ondelete="CASCADE"), primary_key=True),
    Column("canonical_name", Text, nullable=False),
    Column("normalised_name", Text, nullable=False),
    sqlite_strict=True,
)
organisation_alias = Table(
    "organisation_alias",
    metadata,
    Column(
        "organisation_id",
        Text,
        ForeignKey("organisation.object_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("normalised_alias", Text, nullable=False),
    UniqueConstraint("organisation_id", "normalised_alias"),
    sqlite_strict=True,
)
property_table = Table(
    "property",
    metadata,
    Column("object_id", Text, ForeignKey("object.id", ondelete="CASCADE"), primary_key=True),
    Column("normalised_address", Text, nullable=False),
    Column("occupancy", Text),
    CheckConstraint(
        "occupancy IS NULL OR occupancy IN ('rental','occupied')", name="ck_property_occupancy"
    ),
    sqlite_strict=True,
)
account = Table(
    "account",
    metadata,
    Column("object_id", Text, ForeignKey("object.id", ondelete="CASCADE"), primary_key=True),
    Column("account_type", Text, nullable=False),
    Column("suffix", Text, nullable=False),
    Column("issuer_organisation_id", Text, ForeignKey("organisation.object_id"), nullable=False),
    Column("related_subject_id", Text, ForeignKey("object.id"), nullable=False),
    UniqueConstraint("issuer_organisation_id", "account_type", "suffix"),
    sqlite_strict=True,
)
asset = Table(
    "asset",
    metadata,
    Column("object_id", Text, ForeignKey("object.id", ondelete="CASCADE"), primary_key=True),
    Column("make", Text),
    Column("model", Text),
    Column("serial", Text),
    Column("order_reference", Text),
    sqlite_strict=True,
)
Index("uq_asset_serial", asset.c.serial, unique=True, sqlite_where=asset.c.serial.is_not(None))
Index(
    "uq_asset_order_reference",
    asset.c.order_reference,
    unique=True,
    sqlite_where=asset.c.order_reference.is_not(None),
)

evidence_blob = Table(
    "evidence_blob",
    metadata,
    Column("id", Text, primary_key=True),
    Column("sha256", Text, nullable=False, unique=True),
    Column("byte_size", Integer, nullable=False),
    Column("detected_mime", Text, nullable=False),
    Column("relative_path", Text, nullable=False, unique=True),
    Column("created_at", Text, nullable=False),
    CheckConstraint("byte_size >= 0", name="ck_evidence_size"),
    sqlite_strict=True,
)
source_reference = Table(
    "source_reference",
    metadata,
    Column("id", Text, primary_key=True),
    Column("source_kind", Text, nullable=False),
    Column("source_identity", Text, nullable=False),
    Column("evidence_blob_id", Text, ForeignKey("evidence_blob.id"), nullable=False),
    Column("original_name", Text),
    Column("received_metadata", Text, nullable=False, server_default="{}"),
    Column("received_at", Text, nullable=False),
    UniqueConstraint("source_kind", "source_identity"),
    sqlite_strict=True,
)
document = Table(
    "document",
    metadata,
    Column("object_id", Text, ForeignKey("object.id", ondelete="CASCADE"), primary_key=True),
    Column("evidence_blob_id", Text, ForeignKey("evidence_blob.id"), nullable=False, unique=True),
    Column("document_type", Text),
    Column("document_date", Text),
    Column("processing_state", Text, nullable=False, server_default="pending"),
    Column("extractor_version", Text),
    sqlite_strict=True,
)
extraction_signal = Table(
    "extraction_signal",
    metadata,
    Column("id", Text, primary_key=True),
    Column(
        "document_id", Text, ForeignKey("document.object_id", ondelete="CASCADE"), nullable=False
    ),
    Column("signal_type", Text, nullable=False),
    Column("normalised_value", Text, nullable=False),
    Column("display_value", Text, nullable=False),
    Column("source_locator", Text, nullable=False),
    Column("extractor", Text, nullable=False),
    Column("extractor_version", Text, nullable=False),
    UniqueConstraint("document_id", "signal_type", "normalised_value", "source_locator"),
    CheckConstraint(
        "signal_type IN ('issuer_name','property_address','account_suffix','merchant_name',"
        "'purchase_date','amount_minor','make','model','serial','order_reference')",
        name="ck_extraction_signal_type",
    ),
    sqlite_strict=True,
)
proposal = Table(
    "proposal",
    metadata,
    Column("id", Text, primary_key=True),
    Column(
        "document_id", Text, ForeignKey("document.object_id", ondelete="CASCADE"), nullable=False
    ),
    Column("candidate_object_id", Text, ForeignKey("object.id"), nullable=False),
    Column("predicate", Text, nullable=False),
    Column("score", Integer, nullable=False),
    Column("rank", Integer, nullable=False),
    Column("state", Text, nullable=False, server_default="pending"),
    Column("created_at", Text, nullable=False),
    UniqueConstraint("document_id", "candidate_object_id", "predicate"),
    UniqueConstraint("document_id", "rank"),
    CheckConstraint("rank BETWEEN 1 AND 3", name="ck_proposal_rank"),
    CheckConstraint(
        "predicate IN ('concerns_property','purchase_evidence_for')", name="ck_proposal_predicate"
    ),
    CheckConstraint("state IN ('pending','accepted','rejected')", name="ck_proposal_state"),
    sqlite_strict=True,
)
proposal_evidence = Table(
    "proposal_evidence",
    metadata,
    Column("id", Text, primary_key=True),
    Column("proposal_id", Text, ForeignKey("proposal.id", ondelete="CASCADE"), nullable=False),
    Column("evidence_code", Text, nullable=False),
    Column("extraction_signal_id", Text, ForeignKey("extraction_signal.id"), nullable=False),
    Column("matched_object_id", Text, ForeignKey("object.id"), nullable=False),
    Column("validation_version", Text, nullable=False),
    UniqueConstraint("proposal_id", "evidence_code", "extraction_signal_id", "matched_object_id"),
    CheckConstraint(
        "evidence_code IN ('issuer_exact','address_exact','account_suffix_exact',"
        "'merchant_alias_exact','model_exact','serial_exact','order_reference_exact')",
        name="ck_proposal_evidence_code",
    ),
    sqlite_strict=True,
)
review_decision = Table(
    "review_decision",
    metadata,
    Column("id", Text, primary_key=True),
    Column("proposal_id", Text, ForeignKey("proposal.id"), nullable=False),
    Column("reviewer", Text, nullable=False),
    Column("decision", Text, nullable=False),
    Column("idempotency_key", Text, nullable=False, unique=True),
    Column("decided_at", Text, nullable=False),
    CheckConstraint("decision IN ('accepted','rejected')", name="ck_review_decision"),
    sqlite_strict=True,
)
Index(
    "uq_review_accepted",
    review_decision.c.proposal_id,
    unique=True,
    sqlite_where=review_decision.c.decision == "accepted",
)
relation = Table(
    "relation",
    metadata,
    Column("id", Text, primary_key=True),
    Column("document_id", Text, ForeignKey("document.object_id"), nullable=False),
    Column("predicate", Text, nullable=False),
    Column("target_object_id", Text, ForeignKey("object.id"), nullable=False),
    Column("status", Text, nullable=False),
    Column("confirmation_decision_id", Text, ForeignKey("review_decision.id"), nullable=False),
    Column("created_at", Text, nullable=False),
    UniqueConstraint("document_id", "predicate", "target_object_id"),
    CheckConstraint(
        "predicate IN ('concerns_property','purchase_evidence_for')",
        name="ck_relation_predicate",
    ),
    CheckConstraint("status = 'confirmed'", name="ck_relation_status"),
    sqlite_strict=True,
)
evidence_link = Table(
    "evidence_link",
    metadata,
    Column("relation_id", Text, ForeignKey("relation.id", ondelete="CASCADE"), nullable=False),
    Column("proposal_evidence_id", Text, ForeignKey("proposal_evidence.id"), nullable=False),
    UniqueConstraint("relation_id", "proposal_evidence_id"),
    sqlite_strict=True,
)
audit_event = Table(
    "audit_event",
    metadata,
    Column("id", Text, primary_key=True),
    Column("actor", Text, nullable=False),
    Column("action", Text, nullable=False),
    Column("affected_id", Text, nullable=False),
    Column("correlation_key", Text, nullable=False),
    Column("payload", Text, nullable=False),
    Column("payload_hash", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    UniqueConstraint("action", "correlation_key"),
    sqlite_strict=True,
)
processing_job = Table(
    "processing_job",
    metadata,
    Column(
        "document_id", Text, ForeignKey("document.object_id", ondelete="CASCADE"), primary_key=True
    ),
    Column("state", Text, nullable=False),
    Column("attempt_count", Integer, nullable=False, server_default="0"),
    Column("lease_owner", Text),
    Column("lease_expires_at", Text),
    Column("error_code", Text),
    Column("updated_at", Text, nullable=False),
    CheckConstraint("state IN ('pending','processing','complete','failed')", name="ck_job_state"),
    sqlite_strict=True,
)

Index("ix_signal_lookup", extraction_signal.c.signal_type, extraction_signal.c.normalised_value)
Index("ix_property_address", property_table.c.normalised_address)
Index("ix_account_suffix", account.c.suffix)
Index("ix_asset_model", asset.c.model)
Index("ix_proposal_document", proposal.c.document_id)
Index("ix_relation_target", relation.c.target_object_id)
