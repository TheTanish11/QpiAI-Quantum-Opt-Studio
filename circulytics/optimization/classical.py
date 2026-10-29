"""Classical baselines: greedy heuristic and exact reference (HiGHS via SciPy)."""
from __future__ import annotations
import time
import numpy as np
from scipy.optimize import milp, LinearConstraint, Bounds
from .problem import AllocationProblem

DEFER = "DEFERRED"


def greedy(p: AllocationProblem) -> dict:
    t0 = time.perf_counter()
    remaining = {f.id: f.capacity_kg for f in p.facilities}
    assign = {}
    for b in sorted(p.batches, key=lambda b: -b.mass_kg):
        options = [(p.cost(b, f), f.id) for f in p.facilities
                   if p.eligible(b, f) and remaining[f.id] >= b.mass_kg]
        if options:
            _, fid = min(options)
            assign[b.id] = fid
            remaining[fid] -= b.mass_kg
        else:
            assign[b.id] = DEFER
    return {"solver": "classical-greedy", "assignment": assign, "runtime_s": time.perf_counter() - t0}


def exact(p: AllocationProblem) -> dict:
    """Optimal reference solution via MILP (HiGHS). Used to grade other solvers."""
    t0 = time.perf_counter()
    B, F = p.batches, p.facilities
    nb, nf = len(B), len(F)
    nv = nb * nf + nb                      # x_ij then d_i
    c = np.zeros(nv)
    ub = np.ones(nv)
    for i, b in enumerate(B):
        for j, f in enumerate(F):
            c[i * nf + j] = p.cost(b, f)
            if not p.eligible(b, f):
                ub[i * nf + j] = 0
        c[nb * nf + i] = p.defer_cost(b)
    A_eq = np.zeros((nb, nv))
    for i in range(nb):
        A_eq[i, i * nf:(i + 1) * nf] = 1
        A_eq[i, nb * nf + i] = 1
    A_cap = np.zeros((nf, nv))
    for j in range(nf):
        for i, b in enumerate(B):
            A_cap[j, i * nf + j] = b.mass_kg
    cons = [LinearConstraint(A_eq, 1, 1),
            LinearConstraint(A_cap, -np.inf, [f.capacity_kg for f in F])]
    res = milp(c, constraints=cons, integrality=np.ones(nv), bounds=Bounds(np.zeros(nv), ub))
    assign = {}
    if res.success:
        for i, b in enumerate(B):
            assign[b.id] = DEFER
            for j, f in enumerate(F):
                if res.x[i * nf + j] > 0.5:
                    assign[b.id] = f.id
    return {"solver": "classical-exact-milp", "assignment": assign,
            "runtime_s": time.perf_counter() - t0, "status": "optimal" if res.success else "failed"}
