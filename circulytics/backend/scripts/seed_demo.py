"""Seed a clearly SYNTHETIC end-to-end demonstration through the public API.
Usage (from repo root):  python backend/scripts/seed_demo.py
Resets the local SQLite DB first. Demo-only private keys are written to data/synthetic/demo_keys.json.
"""
import json, os, pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "backend")]
dbfile = ROOT / "backend" / "circulytics.sqlite3"
if dbfile.exists():
    dbfile.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{dbfile}"

from fastapi.testclient import TestClient
from app import crypto, db as dbmod
from app.db import Base
import app.models  # noqa
from app.main import app

Base.metadata.create_all(dbmod.engine)
c = TestClient(app)
keys, ids = {}, {}
for name, role in [("Admin Authority", "ADMIN"), ("Aurora Cells (OEM)", "MANUFACTURER"),
                   ("CollectCo Hub", "COLLECTOR"), ("ReLife Repairs", "REPAIRER"),
                   ("GreenLoop Recycling", "RECYCLER"), ("CertiScale Weighbridge", "WEIGHBRIDGE"),
                   ("Independent Auditor", "AUDITOR"), ("Rogue Recycler Ltd", "RECYCLER")]:
    priv, pub = crypto.generate_keypair()
    r = c.post("/organizations", json={"name": name, "role": role, "public_key_hex": pub}); r.raise_for_status()
    keys[name], ids[name] = priv, r.json()["id"]
H = lambda n: {"X-Org-Id": str(ids[n])}
ADMIN, OEM, COL, REC, SCALE, AUD, ROGUE = ("Admin Authority", "Aurora Cells (OEM)", "CollectCo Hub",
    "GreenLoop Recycling", "CertiScale Weighbridge", "Independent Auditor", "Rogue Recycler Ltd")
c.post("/facilities", headers=H(ADMIN), json={"org_id": ids[REC], "name": "GreenLoop Plant 1",
       "licence_chemistries": ["NMC", "LFP"], "capacity_kg": "5000", "processing_cost_per_kg": "8"}).raise_for_status()

def attest(issuer, kind, bid, payload):
    body = {"kind": kind, "subject_type": "battery", "subject_id": bid, "payload": payload}
    r = c.post("/attestations", headers=H(issuer), json={**body, "signature_hex": crypto.sign(keys[issuer], body)})
    r.raise_for_status(); return r.json()["id"]

def step(who, bid, to, att=None):
    r = c.post(f"/batteries/{bid}/transition", headers=H(who), json={"to_state": to, "attestation_id": att}); r.raise_for_status()

def hand(bid, a, b):
    t = c.post("/custody-transfers", headers=H(a), json={"battery_id": bid, "receiver_id": ids[b]}); t.raise_for_status()
    c.post(f"/custody-transfers/{t.json()['id']}/accept", headers=H(b)).raise_for_status()

def process(serial, mass, chem="NMC", advance_all=True):
    bid = c.post("/batteries", headers=H(OEM), json={"serial_number": serial, "chemistry": chem,
                 "rated_capacity_kwh": "64", "mass_kg": mass}).json()["id"]
    step(OEM, bid, "IN_SERVICE"); step(OEM, bid, "RETIRED"); hand(bid, OEM, COL)
    step(COL, bid, "COLLECTED")
    ha = attest(COL, "HEALTH_TEST", bid, {"soh_percent": 61, "note": "synthetic"})
    step(COL, bid, "UNDER_ASSESSMENT", ha); hand(bid, COL, REC)
    step(REC, bid, "RECYCLING_APPROVED", ha)
    wa = attest(SCALE, "WEIGHBRIDGE", bid, {"mass_kg": mass, "note": "synthetic"})
    step(REC, bid, "IN_PROCESSING", wa)
    return bid

# Scenario part 1 - 100 kg battery, recycler reports 82 + 15 + 2 => 1 kg unexplained => FLAGGED
b1 = process("AUR-2031-0001", "100")
pb1 = c.post("/processing-batches", headers=H(REC), json={"facility_id": 1, "battery_ids": [b1]}).json()
c.post(f"/processing-batches/{pb1['id']}/reconcile", headers=H(REC), json={
    "outputs": {"copper": "50", "cobalt": "32"}, "residue_kg": "15", "documented_loss_kg": "2"})
# a claim against the flagged batch is blocked
c.post("/material-claims", headers=H(COL), json={"claim_key": "CLM-FLAGGED", "output_id": 1, "quantity_kg": "10"})

# Scenario part 2 - a clean batch that reconciles, then claims
b2 = process("AUR-2031-0002", "100")
pb2 = c.post("/processing-batches", headers=H(REC), json={"facility_id": 1, "battery_ids": [b2]}).json()
c.post(f"/processing-batches/{pb2['id']}/reconcile", headers=H(REC), json={
    "outputs": {"copper": "50", "cobalt": "32"}, "residue_kg": "15", "documented_loss_kg": "3"})
out_cu = [o for o in c.get("/material-outputs").json() if o["batch_id"] == pb2["id"] and o["material_type"] == "copper"][0]["id"]
c.post("/material-claims", headers=H(COL), json={"claim_key": "CLM-50KG-A", "output_id": out_cu, "quantity_kg": "50", "idempotency_key": "req-1"})
c.post("/material-claims", headers=H(AUD), json={"claim_key": "CLM-50KG-A", "output_id": out_cu, "quantity_kg": "50"})   # duplicate id
c.post("/material-claims", headers=H(AUD), json={"claim_key": "CLM-50KG-B", "output_id": out_cu, "quantity_kg": "5"})    # exceeds balance

# Attacks (all must be rejected and audited)
c.post("/batteries", headers=H(REC), json={"serial_number": "FAKE-1", "chemistry": "NMC", "rated_capacity_kwh": "1", "mass_kg": "1"})
c.post("/batteries", headers=H(OEM), json={"serial_number": "AUR-2031-0001", "chemistry": "NMC", "rated_capacity_kwh": "1", "mass_kg": "1"})
b3 = c.post("/batteries", headers=H(OEM), json={"serial_number": "AUR-2031-0003", "chemistry": "NMC", "rated_capacity_kwh": "64", "mass_kg": "100"}).json()["id"]
c.post(f"/batteries/{b3}/transition", headers=H(OEM), json={"to_state": "RECYCLING_APPROVED"})
bad = {"kind": "HEALTH_TEST", "subject_type": "battery", "subject_id": b3, "payload": {"soh_percent": 99}}
c.post("/attestations", headers=H(COL), json={**bad, "signature_hex": crypto.sign(keys[ROGUE], bad)})
c.post("/custody-transfers", headers=H(COL), json={"battery_id": b3, "receiver_id": ids[REC]})
b4 = c.post("/batteries", headers=H(OEM), json={"serial_number": "AUR-2031-0004", "chemistry": "NMC", "rated_capacity_kwh": "64", "mass_kg": "100"}).json()["id"]
c.post("/custody-transfers", headers=H(OEM), json={"battery_id": b4, "receiver_id": ids[COL]})  # left PROPOSED (pending)

# Allocation runs
for s in ("classical-greedy", "classical-exact", "qpiai-opt"):
    c.post("/allocations/solve", headers=H(ADMIN), json={"solver": s}).raise_for_status()

(ROOT / "data" / "synthetic").mkdir(parents=True, exist_ok=True)
(ROOT / "data" / "synthetic" / "demo_keys.json").write_text(json.dumps(
    {"WARNING": "synthetic demo keys only", "org_ids": ids, "private_keys": keys}, indent=2))
print(json.dumps(c.get("/dashboard").json(), indent=2))
