import json
from decimal import Decimal
from fastapi import FastAPI, Depends, Header, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from . import services as svc, crypto
from .db import Base, engine, get_db
from . import db as dbmod
from .models import (Organization, Battery, StateEvent, CustodyTransfer, Attestation, ProcessingBatch,
                     MaterialOutput, MaterialClaim, AllocationRun, AuditEvent, Facility)
from .allocation import solve_allocation

from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(_app):
    Base.metadata.create_all(dbmod.engine)
    yield


app = FastAPI(title="CIRCULYTICS API",
              description="EV battery lifecycle and material recovery accountability (prototype, synthetic data)",
              version="0.2.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])




@app.exception_handler(svc.Rejected)
async def rejected_handler(request, exc: svc.Rejected):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=exc.code, content={"detail": exc.message})


def actor(x_org_id: int = Header(..., description="Prototype auth: acting organisation id")) -> int:
    return x_org_id


def j(o, *fields):
    return {f: (str(getattr(o, f)) if isinstance(getattr(o, f), Decimal) else getattr(o, f)) for f in fields}


@app.get("/")
def root():
    return {"project": "CIRCULYTICS", "status": "running", "version": app.version, "data": "synthetic"}


@app.get("/health")
def health():
    return {"status": "healthy"}


# ---- organisations ----
class OrgIn(BaseModel):
    name: str
    role: str
    public_key_hex: str
    wallet_address: str | None = None


@app.post("/organizations", status_code=201)
def create_org(body: OrgIn, db: Session = Depends(get_db)):
    o = svc.register_org(db, body.name, body.role, body.public_key_hex, body.wallet_address)
    return j(o, "id", "name", "role", "active")


@app.get("/organizations")
def list_orgs(db: Session = Depends(get_db)):
    return [j(o, "id", "name", "role", "active", "wallet_address") for o in db.scalars(select(Organization))]


@app.post("/organizations/{org_id}/revoke")
def revoke(org_id: int, a: int = Depends(actor), db: Session = Depends(get_db)):
    return j(svc.revoke_org(db, a, org_id), "id", "name", "active")


# ---- batteries ----
class BatteryIn(BaseModel):
    serial_number: str
    chemistry: str
    rated_capacity_kwh: Decimal
    mass_kg: Decimal


def bat(b: Battery):
    return j(b, "id", "serial_number", "manufacturer_id", "chemistry", "rated_capacity_kwh", "mass_kg",
             "state", "custodian_id", "processed", "passport_root")


@app.post("/batteries", status_code=201)
def create_battery(body: BatteryIn, a: int = Depends(actor), db: Session = Depends(get_db)):
    return bat(svc.register_battery(db, a, body.serial_number, body.chemistry, body.rated_capacity_kwh, body.mass_kg))


@app.get("/batteries")
def list_batteries(db: Session = Depends(get_db)):
    return [bat(b) for b in db.scalars(select(Battery).order_by(Battery.id))]


@app.get("/batteries/{battery_id}")
def get_battery(battery_id: int, db: Session = Depends(get_db)):
    b = db.get(Battery, battery_id)
    if not b:
        raise HTTPException(404, "unknown battery")
    return bat(b)


@app.get("/batteries/{battery_id}/history")
def history(battery_id: int, db: Session = Depends(get_db)):
    ev = db.scalars(select(StateEvent).where(StateEvent.battery_id == battery_id).order_by(StateEvent.id)).all()
    tr = db.scalars(select(CustodyTransfer).where(CustodyTransfer.battery_id == battery_id).order_by(CustodyTransfer.id)).all()
    return {"lineage": svc.verify_lineage(db, battery_id),
            "events": [j(e, "id", "from_state", "to_state", "actor_id", "reason", "attestation_id", "event_hash") | {"at": e.at.isoformat()} for e in ev],
            "custody": [j(t, "id", "sender_id", "receiver_id", "status") for t in tr]}


class TransitionIn(BaseModel):
    to_state: str
    reason: str = ""
    attestation_id: int | None = None


@app.post("/batteries/{battery_id}/transition")
def do_transition(battery_id: int, body: TransitionIn, a: int = Depends(actor), db: Session = Depends(get_db)):
    return bat(svc.transition(db, a, battery_id, body.to_state, body.reason, body.attestation_id))


# ---- attestations / evidence ----
class AttIn(BaseModel):
    kind: str
    subject_type: str
    subject_id: int
    payload: dict
    signature_hex: str
    evidence_hash: str | None = None


@app.post("/attestations", status_code=201)
def create_att(body: AttIn, a: int = Depends(actor), db: Session = Depends(get_db)):
    att = svc.submit_attestation(db, a, body.kind, body.subject_type, body.subject_id, body.payload,
                                 body.signature_hex, body.evidence_hash)
    out = j(att, "id", "kind", "subject_type", "subject_id", "issuer_id", "payload_hash", "evidence_hash")
    if att.evidence_hash:
        from .chain import get_bridge
        anchor = get_bridge().anchor_evidence(att.evidence_hash)
        if anchor is not None:
            svc.audit(db, a, "ANCHOR_EVIDENCE", f"attestation:{att.id}", "OK" if anchor["status"] == "CONFIRMED" else "REJECTED",
                      json.dumps(anchor)); db.commit()
            out["chain_anchor"] = anchor
    return out


@app.get("/attestations")
def list_att(db: Session = Depends(get_db)):
    return [j(x, "id", "kind", "subject_type", "subject_id", "issuer_id", "payload_hash", "evidence_hash") for x in db.scalars(select(Attestation))]


@app.post("/attestations/{att_id}/verify-evidence")
async def verify_evidence(att_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    ok = svc.verify_evidence_file(db, att_id, await file.read())
    return {"attestation_id": att_id, "hash_matches": ok}


# ---- custody ----
class TransferIn(BaseModel):
    battery_id: int
    receiver_id: int


@app.post("/custody-transfers", status_code=201)
def propose(body: TransferIn, a: int = Depends(actor), db: Session = Depends(get_db)):
    return j(svc.propose_transfer(db, a, body.battery_id, body.receiver_id), "id", "battery_id", "sender_id", "receiver_id", "status")


@app.post("/custody-transfers/{tid}/accept")
def accept(tid: int, a: int = Depends(actor), db: Session = Depends(get_db)):
    return j(svc.resolve_transfer(db, a, tid, True), "id", "battery_id", "status")


@app.post("/custody-transfers/{tid}/reject")
def reject_t(tid: int, a: int = Depends(actor), db: Session = Depends(get_db)):
    return j(svc.resolve_transfer(db, a, tid, False), "id", "battery_id", "status")


@app.get("/custody-transfers")
def list_tr(db: Session = Depends(get_db)):
    return [j(t, "id", "battery_id", "sender_id", "receiver_id", "status") for t in db.scalars(select(CustodyTransfer))]


# ---- facilities & processing ----
class FacIn(BaseModel):
    org_id: int
    name: str
    licensed: bool = True
    licence_chemistries: list[str]
    capacity_kg: Decimal
    processing_cost_per_kg: Decimal


@app.post("/facilities", status_code=201)
def create_fac(body: FacIn, a: int = Depends(actor), db: Session = Depends(get_db)):
    admin = svc.get_active_org(db, a, "CREATE_FACILITY")
    svc.require_role(db, admin, {"ADMIN"}, "CREATE_FACILITY")
    f = Facility(**body.model_dump())
    db.add(f); db.commit()
    return j(f, "id", "name", "licensed")


@app.get("/facilities")
def list_fac(db: Session = Depends(get_db)):
    return [j(f, "id", "org_id", "name", "licensed", "licence_chemistries", "capacity_kg", "processing_cost_per_kg") for f in db.scalars(select(Facility))]


class BatchIn(BaseModel):
    facility_id: int
    battery_ids: list[int]


@app.post("/processing-batches", status_code=201)
def open_batch(body: BatchIn, a: int = Depends(actor), db: Session = Depends(get_db)):
    return pb(svc.open_processing_batch(db, a, body.facility_id, body.battery_ids))


def pb(b: ProcessingBatch):
    return j(b, "id", "facility_id", "recycler_id", "battery_ids", "incoming_mass_kg", "residue_kg",
             "documented_loss_kg", "tolerance_kg", "discrepancy_kg", "status")


class ReconIn(BaseModel):
    outputs: dict[str, Decimal]
    residue_kg: Decimal
    documented_loss_kg: Decimal
    tolerance_kg: Decimal = Decimal("0.500")


@app.post("/processing-batches/{bid}/reconcile")
def reconcile_batch(bid: int, body: ReconIn, a: int = Depends(actor), db: Session = Depends(get_db)):
    return pb(svc.record_outputs_and_reconcile(db, a, bid, body.outputs, body.residue_kg,
                                               body.documented_loss_kg, body.tolerance_kg))


@app.get("/processing-batches")
def list_batches(db: Session = Depends(get_db)):
    return [pb(b) for b in db.scalars(select(ProcessingBatch))]


@app.get("/material-outputs")
def outputs(db: Session = Depends(get_db)):
    return [j(o, "id", "batch_id", "material_type", "mass_kg", "claimed_kg") |
            {"remaining_kg": str(o.mass_kg - o.claimed_kg)} for o in db.scalars(select(MaterialOutput))]


# ---- claims ----
class ClaimIn(BaseModel):
    claim_key: str
    output_id: int
    quantity_kg: Decimal
    idempotency_key: str | None = None


@app.post("/material-claims", status_code=201)
def claim(body: ClaimIn, a: int = Depends(actor), db: Session = Depends(get_db)):
    return j(svc.submit_claim(db, a, body.claim_key, body.output_id, body.quantity_kg, body.idempotency_key),
             "id", "claim_key", "output_id", "claimant_id", "quantity_kg", "status")


@app.get("/material-claims")
def list_claims(db: Session = Depends(get_db)):
    return [j(c, "id", "claim_key", "output_id", "claimant_id", "quantity_kg", "status") for c in db.scalars(select(MaterialClaim))]


@app.post("/material-claims/{cid}/release")
def release(cid: int, a: int = Depends(actor), db: Session = Depends(get_db)):
    return j(svc.release_claim(db, a, cid), "id", "status")


# ---- allocation ----
class AllocIn(BaseModel):
    solver: str = Field("classical-exact", description="classical-greedy | classical-exact | qpiai-opt")
    problem: dict | None = None


def run_json(r: AllocationRun):
    return {"id": r.id, "solver": r.solver, "status": r.status, "objective": str(r.objective) if r.objective is not None else None,
            "input_hash": r.input_hash, "record_hash": r.record_hash, "record": r.record, "at": r.created_at.isoformat()}


@app.post("/allocations/solve", status_code=201)
def solve(body: AllocIn, a: int = Depends(actor), db: Session = Depends(get_db)):
    from optimization.problem import synthetic_problem
    prob = body.problem or synthetic_problem().to_dict()
    return run_json(solve_allocation(db, a, prob, body.solver))


@app.get("/allocations")
def list_runs(db: Session = Depends(get_db)):
    return [run_json(r) for r in db.scalars(select(AllocationRun).order_by(AllocationRun.id.desc()))]


@app.get("/allocations/{run_id}")
def get_run(run_id: int, db: Session = Depends(get_db)):
    r = db.get(AllocationRun, run_id)
    if not r:
        raise HTTPException(404, "unknown run")
    return run_json(r)


# ---- audit / dashboard ----
@app.get("/audit-events")
def audit_events(db: Session = Depends(get_db)):
    return [j(e, "id", "actor_id", "action", "subject", "outcome", "detail") | {"at": e.at.isoformat()}
            for e in db.scalars(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(300))]


@app.get("/dashboard")
def dashboard(db: Session = Depends(get_db)):
    c = lambda q: db.scalar(q) or 0
    return {
        "data_label": "SYNTHETIC DEMONSTRATION DATA",
        "batteries": c(select(func.count(Battery.id))),
        "awaiting_assessment": c(select(func.count(Battery.id)).where(Battery.state == "UNDER_ASSESSMENT")),
        "processing_batches": c(select(func.count(ProcessingBatch.id))),
        "reconciliation_exceptions": c(select(func.count(ProcessingBatch.id)).where(ProcessingBatch.status == "FLAGGED")),
        "active_claims": c(select(func.count(MaterialClaim.id)).where(MaterialClaim.status == "ACTIVE")),
        "rejected_attempts": c(select(func.count(AuditEvent.id)).where(AuditEvent.outcome == "REJECTED")),
        "duplicate_or_excess_claim_attempts": c(select(func.count(AuditEvent.id)).where(
            AuditEvent.action == "SUBMIT_CLAIM", AuditEvent.outcome == "REJECTED")),
        "allocation_runs": c(select(func.count(AllocationRun.id))),
    }
