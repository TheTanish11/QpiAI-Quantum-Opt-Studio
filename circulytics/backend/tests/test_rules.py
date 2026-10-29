import threading
from decimal import Decimal
from app import crypto
from app.reconciliation import reconcile, ReconciliationError
from app.lifecycle import validate_transition
import pytest


# ---- lifecycle ----
def test_register_battery_ok(env):
    assert env.battery() > 0

def test_duplicate_serial_rejected(env):
    env.battery("DUP")
    r = env.c.post("/batteries", headers=env.h("Maker"), json={"serial_number": "DUP", "chemistry": "NMC",
                                                              "rated_capacity_kwh": "60", "mass_kg": "100"})
    assert r.status_code == 409

def test_non_manufacturer_cannot_register(env):
    r = env.c.post("/batteries", headers=env.h("Recycler"), json={"serial_number": "X", "chemistry": "NMC",
                                                                  "rated_capacity_kwh": "60", "mass_kg": "100"})
    assert r.status_code == 403

def test_valid_transition(env):
    b = env.battery(); assert env.tr(b, "Maker", "IN_SERVICE").status_code == 200

def test_skip_state_rejected(env):
    b = env.battery(); assert env.tr(b, "Maker", "RECYCLING_APPROVED").status_code == 409

def test_validator_unit():
    with pytest.raises(ValueError):
        validate_transition("REGISTERED", "RECONCILED")

def test_missing_attestation_rejected(env):
    b = env.battery(); env.tr(b, "Maker", "IN_SERVICE"); env.tr(b, "Maker", "RETIRED")
    env.handover(b, "Maker", "Collector"); env.tr(b, "Collector", "COLLECTED")
    assert env.tr(b, "Collector", "UNDER_ASSESSMENT").status_code == 422

def test_non_custodian_cannot_transition(env):
    b = env.battery(); env.tr(b, "Maker", "IN_SERVICE"); env.tr(b, "Maker", "RETIRED")
    assert env.tr(b, "Collector", "COLLECTED").status_code == 403   # Collector does not hold custody yet

def test_full_lifecycle_and_lineage(env):
    b = env.battery(); env.to_in_processing(b)
    h = env.c.get(f"/batteries/{b}/history").json()
    assert h["lineage"]["continuous"] and h["lineage"]["events"] == 6


# ---- custody ----
def test_transfer_requires_acceptance_and_stays_pending(env):
    b = env.battery()
    t = env.c.post("/custody-transfers", headers=env.h("Maker"), json={"battery_id": b, "receiver_id": env.ids["Collector"]}).json()
    assert t["status"] == "PROPOSED"
    assert env.c.get(f"/batteries/{b}").json()["custodian_id"] == env.ids["Maker"]

def test_wrong_party_cannot_accept(env):
    b = env.battery()
    t = env.c.post("/custody-transfers", headers=env.h("Maker"), json={"battery_id": b, "receiver_id": env.ids["Collector"]}).json()
    assert env.c.post(f"/custody-transfers/{t['id']}/accept", headers=env.h("Recycler")).status_code == 403

def test_non_custodian_cannot_propose_transfer(env):
    b = env.battery()
    r = env.c.post("/custody-transfers", headers=env.h("Collector"), json={"battery_id": b, "receiver_id": env.ids["Recycler"]})
    assert r.status_code == 403

def test_revoked_receiver_cannot_receive(env):
    b = env.battery()
    env.c.post(f"/organizations/{env.ids['Collector']}/revoke", headers=env.h("Admin"))
    r = env.c.post("/custody-transfers", headers=env.h("Maker"), json={"battery_id": b, "receiver_id": env.ids["Collector"]})
    assert r.status_code == 403

def test_revoked_org_rejected_subsequently(env):
    b = env.battery()
    assert env.c.post(f"/organizations/{env.ids['Maker']}/revoke", headers=env.h("Admin")).status_code == 200
    assert env.tr(b, "Maker", "IN_SERVICE").status_code == 403

def test_only_admin_can_revoke(env):
    assert env.c.post(f"/organizations/{env.ids['Maker']}/revoke", headers=env.h("Recycler")).status_code == 403


# ---- attestations / evidence ----
def test_invalid_signature_rejected(env):
    b = env.battery()
    body = {"kind": "HEALTH_TEST", "subject_type": "battery", "subject_id": b, "payload": {"soh": 80}}
    bad = crypto.sign(env.keys["Recycler"], body)           # signed by the wrong key
    r = env.c.post("/attestations", headers=env.h("Collector"), json={**body, "signature_hex": bad})
    assert r.status_code == 403

def test_tampered_payload_rejected(env):
    b = env.battery()
    body = {"kind": "HEALTH_TEST", "subject_type": "battery", "subject_id": b, "payload": {"soh": 80}}
    sig = crypto.sign(env.keys["Collector"], body)
    body["payload"] = {"soh": 99}
    assert env.c.post("/attestations", headers=env.h("Collector"), json={**body, "signature_hex": sig}).status_code == 403

def test_only_weighbridge_attests_mass(env):
    b = env.battery()
    assert env.attest("Recycler", "WEIGHBRIDGE", b).status_code == 403

def test_evidence_hash_integrity(env):
    b = env.battery(); data = b"inspection photo bytes"
    body = {"kind": "HEALTH_TEST", "subject_type": "battery", "subject_id": b, "payload": {"soh": 80}}
    sig = crypto.sign(env.keys["Collector"], body)
    a = env.c.post("/attestations", headers=env.h("Collector"),
                   json={**body, "signature_hex": sig, "evidence_hash": crypto.sha256_hex(data)}).json()
    ok = env.c.post(f"/attestations/{a['id']}/verify-evidence", files={"file": ("e.bin", data)}).json()
    bad = env.c.post(f"/attestations/{a['id']}/verify-evidence", files={"file": ("e.bin", data + b"x")}).json()
    assert ok["hash_matches"] and not bad["hash_matches"]


# ---- reconciliation ----
def test_within_tolerance_passes():
    r = reconcile(100, {"cu": 82}, 15, 3, "0.5"); assert r.status == "RECONCILED" and r.discrepancy == 0

def test_outside_tolerance_flags():
    r = reconcile(100, {"cu": 82}, 15, 2, "0.5"); assert r.status == "FLAGGED" and r.discrepancy == Decimal("1")

def test_negative_quantity_rejected():
    with pytest.raises(ReconciliationError):
        reconcile(100, {"cu": -5}, 15, 3, "0.5")

def test_batch_reconciled_via_api(env):
    assert env.reconciled_batch()["status"] == "RECONCILED"

def test_batch_flagged_via_api(env):
    assert env.reconciled_batch(loss="2", serial="SN-F")["status"] == "FLAGGED"

def test_unlicensed_chemistry_rejected(env):
    b = env.c.post("/batteries", headers=env.h("Maker"), json={"serial_number": "NCA1", "chemistry": "NCA",
                                                              "rated_capacity_kwh": "60", "mass_kg": "100"}).json()["id"]
    env.to_in_processing(b)
    r = env.c.post("/processing-batches", headers=env.h("Recycler"), json={"facility_id": 1, "battery_ids": [b]})
    assert r.status_code == 403

def test_battery_cannot_be_processed_twice(env):
    b = env.battery(); env.to_in_processing(b)
    assert env.c.post("/processing-batches", headers=env.h("Recycler"), json={"facility_id": 1, "battery_ids": [b]}).status_code == 201
    assert env.c.post("/processing-batches", headers=env.h("Recycler"), json={"facility_id": 1, "battery_ids": [b]}).status_code == 409

def test_other_recycler_cannot_open_batch_on_foreign_facility(env):
    b = env.battery(); env.to_in_processing(b)
    assert env.c.post("/processing-batches", headers=env.h("Recycler2"), json={"facility_id": 1, "battery_ids": [b]}).status_code == 403


# ---- claims ----
def _out(env, **kw):
    env.reconciled_batch(**kw)
    return env.c.get("/material-outputs").json()[0]["id"]

def test_valid_claim(env):
    o = _out(env)
    assert env.c.post("/material-claims", headers=env.h("Collector"), json={"claim_key": "C1", "output_id": o, "quantity_kg": "30"}).status_code == 201

def test_duplicate_claim_key_rejected(env):
    o = _out(env)
    env.c.post("/material-claims", headers=env.h("Collector"), json={"claim_key": "C1", "output_id": o, "quantity_kg": "10"})
    assert env.c.post("/material-claims", headers=env.h("Auditor"), json={"claim_key": "C1", "output_id": o, "quantity_kg": "10"}).status_code == 409

def test_excess_claim_rejected(env):
    o = _out(env)   # copper = 50 kg
    assert env.c.post("/material-claims", headers=env.h("Collector"), json={"claim_key": "A", "output_id": o, "quantity_kg": "35"}).status_code == 201
    assert env.c.post("/material-claims", headers=env.h("Auditor"), json={"claim_key": "B", "output_id": o, "quantity_kg": "35"}).status_code == 409

def test_claim_on_nonexistent_output_rejected(env):
    assert env.c.post("/material-claims", headers=env.h("Collector"), json={"claim_key": "Z", "output_id": 999, "quantity_kg": "1"}).status_code == 404

def test_claims_blocked_on_flagged_batch(env):
    o = _out(env, loss="2", serial="SN-F")
    assert env.c.post("/material-claims", headers=env.h("Collector"), json={"claim_key": "A", "output_id": o, "quantity_kg": "1"}).status_code == 409

def test_idempotent_retry_creates_one_claim(env):
    o = _out(env)
    body = {"claim_key": "R1", "output_id": o, "quantity_kg": "20", "idempotency_key": "req-1"}
    a = env.c.post("/material-claims", headers=env.h("Collector"), json=body)
    b = env.c.post("/material-claims", headers=env.h("Collector"), json=body)
    assert a.json()["id"] == b.json()["id"]
    assert len(env.c.get("/material-claims").json()) == 1
    assert Decimal(env.c.get("/material-outputs").json()[0]["claimed_kg"]) == Decimal("20")

def test_release_frees_balance(env):
    o = _out(env)
    c = env.c.post("/material-claims", headers=env.h("Collector"), json={"claim_key": "A", "output_id": o, "quantity_kg": "50"}).json()
    env.c.post(f"/material-claims/{c['id']}/release", headers=env.h("Collector"))
    assert env.c.post("/material-claims", headers=env.h("Auditor"), json={"claim_key": "B", "output_id": o, "quantity_kg": "50"}).status_code == 201

def test_concurrent_claims_never_exceed_available(env):
    o = _out(env)   # copper 50
    results = []
    def go(i):
        r = env.c.post("/material-claims", headers=env.h("Collector"), json={"claim_key": f"K{i}", "output_id": o, "quantity_kg": "10"})
        results.append(r.status_code)
    ts = [threading.Thread(target=go, args=(i,)) for i in range(12)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert results.count(201) == 5 and results.count(409) == 7
    out = env.c.get("/material-outputs").json()[0]
    assert Decimal(out["claimed_kg"]) <= Decimal(out["mass_kg"])


# ---- allocation ----
def test_exact_allocation_accepted(env):
    r = env.c.post("/allocations/solve", headers=env.h("Admin"), json={"solver": "classical-exact"}).json()
    assert r["status"] == "ACCEPTED" and r["record"]["validation"]["feasible"]

def test_exact_beats_greedy(env):
    g = env.c.post("/allocations/solve", headers=env.h("Admin"), json={"solver": "classical-greedy"}).json()
    e = env.c.post("/allocations/solve", headers=env.h("Admin"), json={"solver": "classical-exact"}).json()
    assert float(e["objective"]) <= float(g["objective"])

def test_qpiai_without_key_is_not_fabricated(env, monkeypatch):
    monkeypatch.delenv("QPIAI_OPT_API_KEY", raising=False)
    r = env.c.post("/allocations/solve", headers=env.h("Admin"), json={"solver": "qpiai-opt"}).json()
    assert r["status"] == "NOT_EXECUTED" and r["objective"] is None

def test_validator_rejects_capacity_and_ineligible():
    from optimization.problem import synthetic_problem
    from optimization.validate import validate
    p = synthetic_problem()
    all_f1 = {b.id: "F1-GreenLoop" for b in p.batches}
    v = validate(p, all_f1); assert not v["feasible"] and any("capacity" in e for e in v["errors"])
    f4 = {b.id: "F4-VoltCycle" for b in p.batches}
    v = validate(p, f4); assert any("not eligible" in e for e in v["errors"])

def test_infeasible_solver_output_is_rejected(env, monkeypatch):
    from app import allocation
    from optimization import classical
    bad = lambda p: {"solver": "classical-greedy", "assignment": {b.id: "F4-VoltCycle" for b in p.batches}, "runtime_s": 0}
    monkeypatch.setattr(classical, "greedy", bad)
    r = env.c.post("/allocations/solve", headers=env.h("Admin"), json={"solver": "classical-greedy"}).json()
    assert r["status"] == "REJECTED" and r["objective"] is None


# ---- audit trail ----
def test_rejections_are_audited(env):
    env.c.post("/batteries", headers=env.h("Recycler"), json={"serial_number": "X", "chemistry": "NMC", "rated_capacity_kwh": "1", "mass_kg": "1"})
    ev = env.c.get("/audit-events").json()
    assert any(e["outcome"] == "REJECTED" and e["action"] == "REGISTER_BATTERY" for e in ev)


# ---- chain bridge (runs only when a local Hardhat node + deployed contract are configured) ----
import os
chain = pytest.mark.skipif(not os.getenv("CIRCULYTICS_ADDRESS"), reason="no local chain configured")
RELAYER_TEST_KEY = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"  # public Hardhat key

@chain
def test_run_and_evidence_anchored_on_chain(env, monkeypatch):
    from app.chain import Bridge
    monkeypatch.setenv("RELAYER_PRIVATE_KEY", RELAYER_TEST_KEY)
    monkeypatch.setenv("BLOCKCHAIN_RPC_URL", os.getenv("BLOCKCHAIN_RPC_URL", "http://127.0.0.1:8545"))
    r = env.c.post("/allocations/solve", headers=env.h("Admin"), json={"solver": "classical-exact"}).json()
    anchor = r["record"]["chain_anchor"]
    assert anchor["status"] == "CONFIRMED"
    assert Bridge().run_is_anchored(r["record_hash"])
    # evidence
    b = env.battery(); data = b"photo"
    body = {"kind": "HEALTH_TEST", "subject_type": "battery", "subject_id": b, "payload": {"soh": 80}}
    sig = crypto.sign(env.keys["Collector"], body)
    a = env.c.post("/attestations", headers=env.h("Collector"),
                   json={**body, "signature_hex": sig, "evidence_hash": crypto.sha256_hex(data)}).json()
    assert a["chain_anchor"]["status"] == "CONFIRMED"
    assert Bridge().evidence_is_anchored(crypto.sha256_hex(data))
    # re-anchoring the same evidence reverts on-chain -> reported as not confirmed
    again = Bridge().anchor_evidence(crypto.sha256_hex(data))
    assert again["status"] in ("FAILED", "REVERTED")

def test_bridge_disabled_by_default(env, monkeypatch):
    monkeypatch.delenv("CIRCULYTICS_ADDRESS", raising=False)
    r = env.c.post("/allocations/solve", headers=env.h("Admin"), json={"solver": "classical-exact"}).json()
    assert "chain_anchor" not in r["record"]
