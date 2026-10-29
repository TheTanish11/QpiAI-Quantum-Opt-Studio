from __future__ import annotations
import datetime as dt
from decimal import Decimal
from sqlalchemy import (
    ForeignKey, Numeric, String, Text, UniqueConstraint, DateTime, Boolean, Integer, JSON,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .db import Base


def now():
    return dt.datetime.now(dt.timezone.utc)


class Organization(Base):
    __tablename__ = "organizations"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    role: Mapped[str] = mapped_column(String(30))  # MANUFACTURER, COLLECTOR, REPAIRER, RECYCLER, AUDITOR, ADMIN, WEIGHBRIDGE
    public_key_hex: Mapped[str] = mapped_column(String(128))  # Ed25519 raw public key
    active: Mapped[bool] = mapped_column(Boolean, default=True)  # revocation flag
    wallet_address: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Facility(Base):
    __tablename__ = "facilities"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"))
    name: Mapped[str] = mapped_column(String(120), unique=True)
    licensed: Mapped[bool] = mapped_column(Boolean, default=True)
    licence_chemistries: Mapped[list] = mapped_column(JSON)  # ["NMC","LFP"]
    capacity_kg: Mapped[Decimal] = mapped_column(Numeric(14, 3))
    processing_cost_per_kg: Mapped[Decimal] = mapped_column(Numeric(12, 4))


class Battery(Base):
    __tablename__ = "batteries"
    __table_args__ = (UniqueConstraint("manufacturer_id", "serial_number", name="uq_battery_serial"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    serial_number: Mapped[str] = mapped_column(String(80))
    manufacturer_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"))
    chemistry: Mapped[str] = mapped_column(String(20))
    rated_capacity_kwh: Mapped[Decimal] = mapped_column(Numeric(10, 3))
    mass_kg: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    state: Mapped[str] = mapped_column(String(30), default="REGISTERED")
    custodian_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"))
    soh_percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    passport_root: Mapped[str | None] = mapped_column(String(66), nullable=True)  # merkle root of attributes
    processed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)


class StateEvent(Base):
    __tablename__ = "state_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    battery_id: Mapped[int] = mapped_column(ForeignKey("batteries.id"))
    from_state: Mapped[str] = mapped_column(String(30))
    to_state: Mapped[str] = mapped_column(String(30))
    actor_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"))
    reason: Mapped[str] = mapped_column(Text, default="")
    attestation_id: Mapped[int | None] = mapped_column(ForeignKey("attestations.id"), nullable=True)
    prev_hash: Mapped[str] = mapped_column(String(64))
    event_hash: Mapped[str] = mapped_column(String(64))
    at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)


class CustodyTransfer(Base):
    __tablename__ = "custody_transfers"
    id: Mapped[int] = mapped_column(primary_key=True)
    battery_id: Mapped[int] = mapped_column(ForeignKey("batteries.id"))
    sender_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"))
    receiver_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"))
    status: Mapped[str] = mapped_column(String(20), default="PROPOSED")  # PROPOSED/ACCEPTED/REJECTED
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Attestation(Base):
    __tablename__ = "attestations"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(40))  # HEALTH_TEST, WEIGHBRIDGE, HANDOVER, PROCESSING
    subject_type: Mapped[str] = mapped_column(String(30))
    subject_id: Mapped[int] = mapped_column(Integer)
    issuer_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"))
    payload: Mapped[dict] = mapped_column(JSON)
    payload_hash: Mapped[str] = mapped_column(String(64))
    signature_hex: Mapped[str] = mapped_column(String(160))
    evidence_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)


class ProcessingBatch(Base):
    __tablename__ = "processing_batches"
    id: Mapped[int] = mapped_column(primary_key=True)
    facility_id: Mapped[int] = mapped_column(ForeignKey("facilities.id"))
    recycler_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"))
    battery_ids: Mapped[list] = mapped_column(JSON)
    incoming_mass_kg: Mapped[Decimal] = mapped_column(Numeric(14, 3))  # sum of passport masses (anchor)
    measured_mass_kg: Mapped[Decimal | None] = mapped_column(Numeric(14, 3), nullable=True)  # weighbridge
    residue_kg: Mapped[Decimal | None] = mapped_column(Numeric(14, 3), nullable=True)
    documented_loss_kg: Mapped[Decimal | None] = mapped_column(Numeric(14, 3), nullable=True)
    tolerance_kg: Mapped[Decimal] = mapped_column(Numeric(10, 3), default=Decimal("0.500"))
    discrepancy_kg: Mapped[Decimal | None] = mapped_column(Numeric(14, 3), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="IN_PROCESSING")  # IN_PROCESSING / RECORDED / RECONCILED / FLAGGED
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)


class MaterialOutput(Base):
    __tablename__ = "material_outputs"
    __table_args__ = (UniqueConstraint("batch_id", "material_type", name="uq_output_batch_material"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("processing_batches.id"))
    material_type: Mapped[str] = mapped_column(String(30))
    mass_kg: Mapped[Decimal] = mapped_column(Numeric(14, 3))
    claimed_kg: Mapped[Decimal] = mapped_column(Numeric(14, 3), default=Decimal("0"))  # guarded counter


class MaterialClaim(Base):
    __tablename__ = "material_claims"
    id: Mapped[int] = mapped_column(primary_key=True)
    claim_key: Mapped[str] = mapped_column(String(80), unique=True)  # client-supplied unique claim id
    idempotency_key: Mapped[str | None] = mapped_column(String(80), unique=True, nullable=True)
    output_id: Mapped[int] = mapped_column(ForeignKey("material_outputs.id"))
    claimant_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"))
    quantity_kg: Mapped[Decimal] = mapped_column(Numeric(14, 3))
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")  # ACTIVE / RELEASED
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)


class AllocationRun(Base):
    __tablename__ = "allocation_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    solver: Mapped[str] = mapped_column(String(40))
    input_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30))  # ACCEPTED / REJECTED
    objective: Mapped[Decimal | None] = mapped_column(Numeric(16, 4), nullable=True)
    record: Mapped[dict] = mapped_column(JSON)
    record_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    action: Mapped[str] = mapped_column(String(60))
    subject: Mapped[str] = mapped_column(String(80), default="")
    outcome: Mapped[str] = mapped_column(String(20))  # OK / REJECTED
    detail: Mapped[str] = mapped_column(Text, default="")
    at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)
