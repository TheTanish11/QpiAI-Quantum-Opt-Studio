"""QAOA experiment on a SMALL reduced allocation instance, run through the QpiAI Quantum SDK.

Honest scope: this does NOT solve the 20-batch problem. It encodes a 3-batch x 2-facility
(+defer) instance as a QUBO/Ising cost Hamiltonian, runs a depth-1 QAOA circuit, decodes the
measured bitstrings, and evaluates each against the ORIGINAL constraints. Infeasible bitstrings
are counted and reported, not hidden.

Execution device defaults to 'QpiAI-QSV-Local' (the SDK's local statevector simulator, needs no
key). If QPIAI_QCLOUD_API_KEY is set, pass device_name='QpiAI-QSV-Simulator' to run on QCloud.
"""
from __future__ import annotations
import itertools
import math
import os
import time
from importlib.metadata import version

from ..problem import AllocationProblem, BatchSpec, FacilitySpec
from ..validate import validate
from ..classical import exact, DEFER


def reduced_instance() -> AllocationProblem:
    batches = [BatchSpec("Q1", 200, "NMC"), BatchSpec("Q2", 300, "NMC"), BatchSpec("Q3", 250, "LFP")]
    facilities = [FacilitySpec("QF1", 500, 9.0, 0.02, ("NMC", "LFP")),
                  FacilitySpec("QF2", 400, 7.0, 0.03, ("NMC",))]
    dist = {"Q1": {"QF1": 100, "QF2": 300}, "Q2": {"QF1": 200, "QF2": 100}, "Q3": {"QF1": 150, "QF2": 90}}
    return AllocationProblem(batches, facilities, dist)


def build_qubo(p: AllocationProblem):
    """Variables: x_ij for eligible pairs, d_i for each batch. One-hot per batch via penalty;
    capacity handled by PAIRWISE exclusion penalties (exact for this 3-batch instance because
    only pairs can overflow; final evaluation re-checks the true constraint anyway)."""
    var = {}
    for i, b in enumerate(p.batches):
        for j, f in enumerate(p.facilities):
            if p.eligible(b, f):
                var[("x", i, j)] = len(var)
        var[("d", i)] = len(var)
    n = len(var)
    Q = {}
    def add(a, b_, w):
        k = (min(a, b_), max(a, b_))
        Q[k] = Q.get(k, 0.0) + w
    scale = max(max(p.cost(b, f) for b in p.batches for f in p.facilities), 1.0)
    A = 3 * max(max(p.defer_cost(b) for b in p.batches), scale)
    for key, idx in var.items():
        c = p.cost(p.batches[key[1]], p.facilities[key[2]]) if key[0] == "x" else p.defer_cost(p.batches[key[1]])
        add(idx, idx, c)
    for i in range(len(p.batches)):
        idxs = [idx for key, idx in var.items() if key[1] == i]
        for a in idxs:
            add(a, a, -A)
        for a, b_ in itertools.combinations(idxs, 2):
            add(a, b_, 2 * A)
    for j, f in enumerate(p.facilities):
        xs = [(key[1], idx) for key, idx in var.items() if key[0] == "x" and key[2] == j]
        for (i1, a), (i2, b_) in itertools.combinations(xs, 2):
            if p.batches[i1].mass_kg + p.batches[i2].mass_kg > f.capacity_kg:
                add(a, b_, A)
    return var, Q, n


def qubo_to_ising(Q, n):
    h = [0.0] * n; J = {}; off = 0.0
    for (a, b), w in Q.items():
        if a == b:
            h[a] -= w / 2; off += w / 2
        else:
            J[(a, b)] = J.get((a, b), 0.0) + w / 4
            h[a] -= w / 4; h[b] -= w / 4; off += w / 4
    return h, J, off


def energy(Q, bits):
    return sum(w * bits[a] * bits[b] for (a, b), w in Q.items())


def decode_bits(p, var, bits):
    out = {}
    for i, b in enumerate(p.batches):
        chosen = [key for key, idx in var.items() if key[1] == i and bits[idx]]
        if len(chosen) != 1:
            out[b.id] = "INVALID_ONEHOT"
        else:
            k = chosen[0]
            out[b.id] = DEFER if k[0] == "d" else p.facilities[k[2]].id
    return out


def qaoa_circuit(h, J, gamma, beta):
    import qpiai_quantum as q
    n = len(h)
    c = q.Circuit(n)
    for k in range(n):
        c.h(k)
    for (a, b), w in J.items():
        c.rzz(a, b, 2 * gamma * w)
    for k in range(n):
        c.rz(k, 2 * gamma * h[k])
        c.rx(k, 2 * beta)
    c.measure_all()
    return c


def run_experiment(device_name="QpiAI-QSV-Local", shots=1000, grid=5) -> dict:
    p = reduced_instance()
    var, Q, n = build_qubo(p)
    h, J, _ = qubo_to_ising(Q, n)
    ref = exact(p)
    ref_val = validate(p, ref["assignment"])
    rec = {"experiment": "QAOA p=1", "device": device_name, "n_qubits": n, "shots": shots,
           "sdk": f"qpiai-quantum {version('qpiai-quantum')}", "input_hash": p.input_hash(),
           "classical_reference": {"assignment": ref["assignment"], "objective": ref_val["objective"]},
           "quantum_advantage_claimed": False}
    # key-free local simulator vs QCloud: pass credentials only for cloud devices
    kwargs = {}
    if device_name != "QpiAI-QSV-Local" and os.getenv("QPIAI_QCLOUD_API_KEY"):
        kwargs["access_token"] = os.getenv("QPIAI_QCLOUD_API_KEY")
    elif device_name != "QpiAI-QSV-Local":
        rec.update(status="NOT_EXECUTED", reason="QPIAI_QCLOUD_API_KEY not set for a cloud device")
        return rec
    t0 = time.perf_counter()
    best = None
    try:
        # coarse parameter search; each point is a real circuit execution
        for gi in range(1, grid + 1):
            for bi in range(1, grid + 1):
                gamma, beta = gi * math.pi / (grid * 20), bi * math.pi / (grid * 2)
                res = qaoa_circuit(h, J, gamma, beta).run(shots=shots // 2, device_name=device_name, **kwargs)
                counts = res.get_counts()
                tot = sum(counts.values())
                mean_e = sum(energy(Q, [int(ch) for ch in reversed(s)]) * c for s, c in counts.items()) / tot
                if best is None or mean_e < best[0]:
                    best = (mean_e, gamma, beta)
        _, gamma, beta = best
        res = qaoa_circuit(h, J, gamma, beta).run(shots=shots, device_name=device_name, **kwargs)
        counts = res.get_counts()
    except Exception as e:
        rec.update(status="FAILED", error=f"{type(e).__name__}: {e}")
        return rec
    total = sum(counts.values())
    feasible_shots, best_feasible = 0, None
    for s, c in counts.items():
        bits = [int(ch) for ch in reversed(s)]          # qubit 0 is the right-most character
        assign = decode_bits(p, var, bits)
        v = validate(p, assign) if "INVALID_ONEHOT" not in assign.values() else {"feasible": False}
        if v["feasible"]:
            feasible_shots += c
            if best_feasible is None or v["objective"] < best_feasible["objective"]:
                best_feasible = {"assignment": assign, "objective": v["objective"], "shots": c}
    rec.update(status="EXECUTED", wall_time_s=round(time.perf_counter() - t0, 3),
               tuned_gamma=best[1], tuned_beta=best[2],
               distinct_bitstrings=len(counts), feasible_fraction=round(feasible_shots / total, 4),
               best_feasible_candidate=best_feasible,
               matches_classical_optimum=bool(best_feasible and abs(best_feasible["objective"] - ref_val["objective"]) < 1e-6),
               top_counts=dict(sorted(counts.items(), key=lambda kv: -kv[1])[:8]))
    return rec
