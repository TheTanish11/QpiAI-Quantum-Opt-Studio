import sys, pathlib
ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "backend"))
import pytest
from fastapi.testclient import TestClient
from app import db as dbmod, crypto
from app.db import Base
import app.models  # noqa: register tables


@pytest.fixture()
def env(tmp_path):
    dbmod.reset_engine(f"sqlite:///{tmp_path}/t.sqlite3")
    Base.metadata.create_all(dbmod.engine)
    from app.main import app
    client = TestClient(app)
    keys, ids = {}, {}
    for name, role in [("Admin", "ADMIN"), ("Maker", "MANUFACTURER"), ("Collector", "COLLECTOR"),
                       ("Repairer", "REPAIRER"), ("Recycler", "RECYCLER"), ("Scale", "WEIGHBRIDGE"),
                       ("Auditor", "AUDITOR"), ("Recycler2", "RECYCLER")]:
        priv, pub = crypto.generate_keypair()
        r = client.post("/organizations", json={"name": name, "role": role, "public_key_hex": pub})
        assert r.status_code == 201, r.text
        keys[name], ids[name] = priv, r.json()["id"]
    h = lambda n: {"X-Org-Id": str(ids[n])}
    client.post("/facilities", headers=h("Admin"), json={
        "org_id": ids["Recycler"], "name": "RecyclerPlant", "licence_chemistries": ["NMC", "LFP"],
        "capacity_kg": "5000", "processing_cost_per_kg": "8"})

    class E: pass
    e = E(); e.c, e.keys, e.ids, e.h = client, keys, ids, h

    def attest(issuer, kind, battery_id, payload=None):
        body = {"kind": kind, "subject_type": "battery", "subject_id": battery_id, "payload": payload or {"ok": True}}
        sig = crypto.sign(keys[issuer], body)
        return e.c.post("/attestations", headers=h(issuer), json={**body, "signature_hex": sig})
    e.attest = attest

    def battery(serial="SN-1", mass="100"):
        r = e.c.post("/batteries", headers=h("Maker"), json={"serial_number": serial, "chemistry": "NMC",
                                                            "rated_capacity_kwh": "60", "mass_kg": mass})
        assert r.status_code == 201, r.text
        return r.json()["id"]
    e.battery = battery

    def tr(bid, who, to, att=None):
        return e.c.post(f"/batteries/{bid}/transition", headers=h(who),
                        json={"to_state": to, "attestation_id": att})
    e.tr = tr

    def handover(bid, frm, to):
        t = e.c.post("/custody-transfers", headers=h(frm), json={"battery_id": bid, "receiver_id": ids[to]})
        assert t.status_code == 201, t.text
        a = e.c.post(f"/custody-transfers/{t.json()['id']}/accept", headers=h(to))
        assert a.status_code == 200, a.text
    e.handover = handover

    def to_in_processing(bid):
        """Drive one battery REGISTERED -> IN_PROCESSING legitimately."""
        assert e.tr(bid, "Maker", "IN_SERVICE").status_code == 200
        assert e.tr(bid, "Maker", "RETIRED").status_code == 200
        e.handover(bid, "Maker", "Collector")
        assert e.tr(bid, "Collector", "COLLECTED").status_code == 200
        ha = e.attest("Collector", "HEALTH_TEST", bid).json()["id"]
        assert e.tr(bid, "Collector", "UNDER_ASSESSMENT", ha).status_code == 200
        e.handover(bid, "Collector", "Recycler")
        assert e.tr(bid, "Recycler", "RECYCLING_APPROVED", ha).status_code == 200
        wa = e.attest("Scale", "WEIGHBRIDGE", bid).json()["id"]
        r = e.tr(bid, "Recycler", "IN_PROCESSING", wa)
        assert r.status_code == 200, r.text
    e.to_in_processing = to_in_processing

    def reconciled_batch(mass="100", outputs=None, residue="15", loss="3", serial="SN-R"):
        bid = e.battery(serial, mass); e.to_in_processing(bid)
        b = e.c.post("/processing-batches", headers=h("Recycler"), json={"facility_id": 1, "battery_ids": [bid]})
        assert b.status_code == 201, b.text
        r = e.c.post(f"/processing-batches/{b.json()['id']}/reconcile", headers=h("Recycler"),
                     json={"outputs": outputs or {"copper": "50", "cobalt": "32"}, "residue_kg": residue,
                           "documented_loss_kg": loss})
        assert r.status_code == 200, r.text
        return r.json()
    e.reconciled_batch = reconciled_batch
    return e
