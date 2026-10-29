"""Business rules. All invariants are enforced here (and by DB constraints),
never by the frontend."""
from decimal import Decimal
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import crypto
from .lifecycle import validate_transition, TRANSITION_ROLES, REQUIRED_ATTESTATION
from .models import (
    Organization, Battery, StateEvent, CustodyTransfer, Attestation, ProcessingBatch,
    MaterialOutput, MaterialClaim, AuditEvent, Facility, AllocationRun,
)
from .reconciliation import reconcile, ReconciliationError

D = Decimal


class Rejected(Exception):
    """A business rule refused the operation. Mapped to HTTP 4xx."""
    def __init__(self, message: str, code: int = 400):
        super().__init__(message)
        self.message = message
        self.code = code


def audit(db: Session, actor_id, action, subject, outcome, detail=""):
    db.add(AuditEvent(actor_id=actor_id, action=action, subject=str(subject), outcome=outcome, detail=detail))


def reject(db: Session, actor_id, action, subject, message, code=400):
    """Record a rejected attempt (in its own commit) and raise."""
    db.rollback()
    audit(db, actor_id, action, subject, "REJECTED", message)
    db.commit()
    raise Rejected(message, code)


def get_active_org(db: Session, org_id: int, action: str) -> Organization:
    org = db.get(Organization, org_id)
    if org is None:
        reject(db, org_id, action, "-", "unknown organisation", 401)
    if not org.active:
        reject(db, org_id, action, "-", "organisation authorisation revoked", 403)
    return org


def require_role(db, org: Organization, allowed: set, action: str, subject="-"):
    if org.role not in allowed:
        reject(db, org.id, action, subject, f"role {org.role} not permitted for {action}", 403)


# ---------- organisations ----------
def register_org(db, name, role, public_key_hex, wallet=None) -> Organization:
    org = Organization(name=name, role=role, public_key_hex=public_key_hex, wallet_address=wallet)
    db.add(org)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise Rejected("organisation name already exists", 409)
    audit(db, org.id, "REGISTER_ORG", org.id, "OK", role)
    db.commit()
    return org


def revoke_org(db, admin_id, org_id):
    admin = get_active_org(db, admin_id, "REVOKE_ORG")
    require_role(db, admin, {"ADMIN"}, "REVOKE_ORG", org_id)
    org = db.get(Organization, org_id)
    if org is None:
        raise Rejected("unknown organisation", 404)
    org.active = False
    audit(db, admin_id, "REVOKE_ORG", org_id, "OK")
    db.commit()
    return org


# ---------- batteries ----------
def register_battery(db, actor_id, serial, chemistry, capacity_kwh, mass_kg, attrs=None) -> Battery:
    actor = get_active_org(db, actor_id, "REGISTER_BATTERY")
    require_role(db, actor, {"MANUFACTURER"}, "REGISTER_BATTERY", serial)
    mass = D(str(mass_kg))
    if mass <= 0:
        reject(db, actor_id, "REGISTER_BATTERY", serial, "mass must be positive", 422)
    b = Battery(
        serial_number=serial, manufacturer_id=actor.id, chemistry=chemistry,
        rated_capacity_kwh=D(str(capacity_kwh)), mass_kg=mass, custodian_id=actor.id,
        passport_root=crypto.merkle_root({"serial": serial, "chemistry": chemistry,
                                          "capacity_kwh": str(capacity_kwh), "mass_kg": str(mass),
                                          **(attrs or {})}),
    )
    db.add(b)
    try:
        db.flush()
    except IntegrityError:
        reject(db, actor_id, "REGISTER_BATTERY", serial, "duplicate battery serial for this manufacturer", 409)
    audit(db, actor_id, "REGISTER_BATTERY", b.id, "OK", serial)
    db.commit()
    return b


# ---------- attestations ----------
def submit_attestation(db, issuer_id, kind, subject_type, subject_id, payload, signature_hex, evidence_hash=None):
    issuer = get_active_org(db, issuer_id, "SUBMIT_ATTESTATION")
    body = {"kind": kind, "subject_type": subject_type, "subject_id": subject_id, "payload": payload}
    if not crypto.verify(issuer.public_key_hex, body, signature_hex):
        reject(db, issuer_id, "SUBMIT_ATTESTATION", f"{subject_type}:{subject_id}", "invalid signature", 403)
    if kind == "HEALTH_TEST" and issuer.role not in {"REPAIRER", "RECYCLER", "COLLECTOR", "MANUFACTURER", "AUDITOR"}:
        reject(db, issuer_id, "SUBMIT_ATTESTATION", f"{subject_type}:{subject_id}", "issuer role cannot attest health", 403)
    if kind == "WEIGHBRIDGE" and issuer.role not in {"WEIGHBRIDGE", "AUDITOR"}:
        reject(db, issuer_id, "SUBMIT_ATTESTATION", f"{subject_type}:{subject_id}", "only weighbridge/auditor may attest mass", 403)
    att = Attestation(kind=kind, subject_type=subject_type, subject_id=subject_id, issuer_id=issuer_id,
                      payload=payload, payload_hash=crypto.hash_obj(body), signature_hex=signature_hex,
                      evidence_hash=evidence_hash)
    db.add(att)
    audit(db, issuer_id, "SUBMIT_ATTESTATION", f"{subject_type}:{subject_id}", "OK", kind)
    db.commit()
    return att


def verify_evidence_file(db, attestation_id, file_bytes: bytes) -> bool:
    att = db.get(Attestation, attestation_id)
    if att is None or att.evidence_hash is None:
        return False
    ok = crypto.sha256_hex(file_bytes) == att.evidence_hash
    audit(db, None, "VERIFY_EVIDENCE", attestation_id, "OK" if ok else "REJECTED",
          "hash match" if ok else "integrity failure: file differs from recorded hash")
    db.commit()
    return ok


# ---------- lifecycle ----------
def _last_event_hash(db, battery_id) -> str:
    last = db.scalars(select(StateEvent).where(StateEvent.battery_id == battery_id)
                      .order_by(StateEvent.id.desc())).first()
    return last.event_hash if last else "0" * 64


def transition(db, actor_id, battery_id, to_state, reason="", attestation_id=None) -> Battery:
    actor = get_active_org(db, actor_id, "TRANSITION")
    b = db.get(Battery, battery_id)
    if b is None:
        raise Rejected("unknown battery", 404)
    subj = f"battery:{battery_id}"
    try:
        validate_transition(b.state, to_state)
    except ValueError as e:
        reject(db, actor_id, "TRANSITION", subj, str(e), 409)
    require_role(db, actor, TRANSITION_ROLES.get(to_state, set()), "TRANSITION", subj)
    # custody: only the current custodian (or admin/auditor-led reconcile) may move the battery
    if b.custodian_id != actor.id and actor.role not in {"ADMIN", "AUDITOR"}:
        reject(db, actor_id, "TRANSITION", subj, "actor is not the current custodian", 403)
    need = REQUIRED_ATTESTATION.get(to_state)
    if need:
        att = db.get(Attestation, attestation_id) if attestation_id else None
        if att is None or att.kind != need or att.subject_type != "battery" or att.subject_id != battery_id:
            reject(db, actor_id, "TRANSITION", subj, f"missing {need} attestation", 422)
    if to_state == "IN_PROCESSING" and b.processed:
        reject(db, actor_id, "TRANSITION", subj, "battery already processed", 409)
    prev = _last_event_hash(db, battery_id)
    body = {"battery": battery_id, "from": b.state, "to": to_state, "actor": actor_id,
            "att": attestation_id, "prev": prev}
    db.add(StateEvent(battery_id=battery_id, from_state=b.state, to_state=to_state, actor_id=actor_id,
                      reason=reason, attestation_id=attestation_id, prev_hash=prev,
                      event_hash=crypto.hash_obj(body)))
    b.state = to_state
    audit(db, actor_id, "TRANSITION", subj, "OK", to_state)
    db.commit()
    return b


def verify_lineage(db, battery_id) -> dict:
    """Recompute the hash chain of a battery's state events (lineage continuity)."""
    events = db.scalars(select(StateEvent).where(StateEvent.battery_id == battery_id).order_by(StateEvent.id)).all()
    prev = "0" * 64
    expect_state = "REGISTERED"
    for e in events:
        body = {"battery": battery_id, "from": e.from_state, "to": e.to_state, "actor": e.actor_id,
                "att": e.attestation_id, "prev": prev}
        if e.prev_hash != prev or e.event_hash != crypto.hash_obj(body) or e.from_state != expect_state:
            return {"continuous": False, "broken_at_event": e.id, "events": len(events)}
        prev, expect_state = e.event_hash, e.to_state
    return {"continuous": True, "events": len(events), "head": prev}


# ---------- custody ----------
def propose_transfer(db, actor_id, battery_id, receiver_id) -> CustodyTransfer:
    actor = get_active_org(db, actor_id, "PROPOSE_TRANSFER")
    b = db.get(Battery, battery_id)
    if b is None:
        raise Rejected("unknown battery", 404)
    subj = f"battery:{battery_id}"
    if b.custodian_id != actor.id:
        reject(db, actor_id, "PROPOSE_TRANSFER", subj, "only the current custodian may propose a transfer", 403)
    recv = db.get(Organization, receiver_id)
    if recv is None or not recv.active:
        reject(db, actor_id, "PROPOSE_TRANSFER", subj, "receiver unknown or not authorised", 403)
    if recv.id == actor.id:
        reject(db, actor_id, "PROPOSE_TRANSFER", subj, "sender and receiver must differ", 422)
    pending = db.scalars(select(CustodyTransfer).where(CustodyTransfer.battery_id == battery_id,
                                                       CustodyTransfer.status == "PROPOSED")).first()
    if pending:
        reject(db, actor_id, "PROPOSE_TRANSFER", subj, "a transfer is already pending", 409)
    t = CustodyTransfer(battery_id=battery_id, sender_id=actor.id, receiver_id=recv.id)
    db.add(t)
    audit(db, actor_id, "PROPOSE_TRANSFER", subj, "OK", f"to org {recv.id}")
    db.commit()
    return t


def resolve_transfer(db, actor_id, transfer_id, accept: bool) -> CustodyTransfer:
    actor = get_active_org(db, actor_id, "RESOLVE_TRANSFER")
    t = db.get(CustodyTransfer, transfer_id)
    if t is None:
        raise Rejected("unknown transfer", 404)
    subj = f"transfer:{transfer_id}"
    if t.receiver_id != actor.id:
        reject(db, actor_id, "RESOLVE_TRANSFER", subj, "only the receiving organisation may accept/reject", 403)
    if t.status != "PROPOSED":
        reject(db, actor_id, "RESOLVE_TRANSFER", subj, f"transfer already {t.status}", 409)
    import datetime as dt
    t.status = "ACCEPTED" if accept else "REJECTED"
    t.resolved_at = dt.datetime.now(dt.timezone.utc)
    if accept:
        b = db.get(Battery, t.battery_id)
        if b.custodian_id != t.sender_id:
            reject(db, actor_id, "RESOLVE_TRANSFER", subj, "sender no longer holds custody", 409)
        b.custodian_id = actor.id
    audit(db, actor_id, "RESOLVE_TRANSFER", subj, "OK", t.status)
    db.commit()
    return t


# ---------- processing & reconciliation ----------
def open_processing_batch(db, actor_id, facility_id, battery_ids, attestation_id=None) -> ProcessingBatch:
    actor = get_active_org(db, actor_id, "OPEN_BATCH")
    require_role(db, actor, {"RECYCLER"}, "OPEN_BATCH")
    fac = db.get(Facility, facility_id)
    if fac is None or fac.org_id != actor.id:
        reject(db, actor_id, "OPEN_BATCH", f"facility:{facility_id}", "facility does not belong to actor", 403)
    if not fac.licensed:
        reject(db, actor_id, "OPEN_BATCH", f"facility:{facility_id}", "facility is not licensed", 403)
    batteries = [db.get(Battery, i) for i in battery_ids]
    if not batteries or any(b is None for b in batteries):
        reject(db, actor_id, "OPEN_BATCH", "-", "unknown battery in batch", 404)
    total = D("0")
    for b in batteries:
        if b.custodian_id != actor.id:
            reject(db, actor_id, "OPEN_BATCH", f"battery:{b.id}", "recycler does not hold custody", 403)
        if b.state != "IN_PROCESSING":
            reject(db, actor_id, "OPEN_BATCH", f"battery:{b.id}", "battery must be IN_PROCESSING", 409)
        if b.processed:
            reject(db, actor_id, "OPEN_BATCH", f"battery:{b.id}", "battery already processed", 409)
        if b.chemistry not in fac.licence_chemistries:
            reject(db, actor_id, "OPEN_BATCH", f"battery:{b.id}", f"facility not licensed for {b.chemistry}", 403)
        total += b.mass_kg
    for b in batteries:
        b.processed = True
    batch = ProcessingBatch(facility_id=facility_id, recycler_id=actor.id, battery_ids=list(battery_ids),
                            incoming_mass_kg=total)
    db.add(batch)
    audit(db, actor_id, "OPEN_BATCH", "-", "OK", f"{len(batteries)} batteries, {total} kg")
    db.commit()
    return batch


def record_outputs_and_reconcile(db, actor_id, batch_id, outputs: dict, residue, loss, tolerance="0.500",
                                 weighbridge_attestation_id=None) -> ProcessingBatch:
    actor = get_active_org(db, actor_id, "RECORD_OUTPUTS")
    batch = db.get(ProcessingBatch, batch_id)
    if batch is None:
        raise Rejected("unknown batch", 404)
    subj = f"batch:{batch_id}"
    if batch.recycler_id != actor.id:
        reject(db, actor_id, "RECORD_OUTPUTS", subj, "not the batch's recycler", 403)
    if batch.status != "IN_PROCESSING":
        reject(db, actor_id, "RECORD_OUTPUTS", subj, "batch already recorded", 409)
    try:
        res = reconcile(batch.incoming_mass_kg, outputs, residue, loss, tolerance)
    except ReconciliationError as e:
        reject(db, actor_id, "RECORD_OUTPUTS", subj, str(e), 422)
    for mat, mass in outputs.items():
        db.add(MaterialOutput(batch_id=batch.id, material_type=mat, mass_kg=D(str(mass))))
    batch.residue_kg, batch.documented_loss_kg = D(str(residue)), D(str(loss))
    batch.tolerance_kg, batch.discrepancy_kg = D(str(tolerance)), res.discrepancy
    batch.status = res.status
    audit(db, actor_id, "RECONCILE", subj, "OK" if res.within_tolerance else "REJECTED",
          f"D={res.discrepancy} tol={res.tolerance} -> {res.status}")
    db.commit()
    return batch


# ---------- claims ----------
def submit_claim(db, actor_id, claim_key, output_id, quantity_kg, idempotency_key=None) -> MaterialClaim:
    actor = get_active_org(db, actor_id, "SUBMIT_CLAIM")
    subj = f"output:{output_id}"
    if idempotency_key:
        prior = db.scalars(select(MaterialClaim).where(MaterialClaim.idempotency_key == idempotency_key)).first()
        if prior is not None:
            return prior  # retry of an already-processed request: no new business operation
    qty = D(str(quantity_kg))
    if qty <= 0:
        reject(db, actor_id, "SUBMIT_CLAIM", subj, "quantity must be positive", 422)
    out = db.get(MaterialOutput, output_id)
    if out is None:
        reject(db, actor_id, "SUBMIT_CLAIM", subj, "claim must reference a real material output", 404)
    batch = db.get(ProcessingBatch, out.batch_id)
    if batch.status == "FLAGGED":
        reject(db, actor_id, "SUBMIT_CLAIM", subj, "batch flagged for investigation; claims blocked", 409)
    if batch.status != "RECONCILED":
        reject(db, actor_id, "SUBMIT_CLAIM", subj, "batch not reconciled", 409)
    if db.scalars(select(MaterialClaim).where(MaterialClaim.claim_key == claim_key)).first():
        reject(db, actor_id, "SUBMIT_CLAIM", subj, "duplicate claim identifier", 409)
    # Atomic guarded increment: succeeds only if it keeps claimed <= available. Safe under concurrency
    # because the UPDATE's WHERE clause is evaluated inside the database transaction.
    res = db.execute(
        update(MaterialOutput)
        .where(MaterialOutput.id == output_id, MaterialOutput.claimed_kg + qty <= MaterialOutput.mass_kg)
        .values(claimed_kg=MaterialOutput.claimed_kg + qty)
    )
    if res.rowcount != 1:
        reject(db, actor_id, "SUBMIT_CLAIM", subj, "claim exceeds unclaimed balance", 409)
    claim = MaterialClaim(claim_key=claim_key, idempotency_key=idempotency_key, output_id=output_id,
                          claimant_id=actor.id, quantity_kg=qty)
    db.add(claim)
    try:
        db.flush()
    except IntegrityError:
        reject(db, actor_id, "SUBMIT_CLAIM", subj, "duplicate claim identifier", 409)
    audit(db, actor_id, "SUBMIT_CLAIM", subj, "OK", f"{qty} kg as {claim_key}")
    db.commit()
    return claim


def release_claim(db, actor_id, claim_id):
    actor = get_active_org(db, actor_id, "RELEASE_CLAIM")
    c = db.get(MaterialClaim, claim_id)
    if c is None:
        raise Rejected("unknown claim", 404)
    if c.claimant_id != actor.id:
        reject(db, actor_id, "RELEASE_CLAIM", f"claim:{claim_id}", "not the claimant", 403)
    if c.status != "ACTIVE":
        reject(db, actor_id, "RELEASE_CLAIM", f"claim:{claim_id}", "claim not active", 409)
    c.status = "RELEASED"
    db.execute(update(MaterialOutput).where(MaterialOutput.id == c.output_id)
               .values(claimed_kg=MaterialOutput.claimed_kg - c.quantity_kg))
    audit(db, actor_id, "RELEASE_CLAIM", f"claim:{claim_id}", "OK")
    db.commit()
    return c
