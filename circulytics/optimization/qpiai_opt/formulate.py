"""Build the allocation problem as a QpiAI Opt `Model` (binary MILP; compilable to QUBO)."""
from __future__ import annotations
from qpiai_opt.opt_modeller import Model, Binary, quicksum
from ..problem import AllocationProblem


def build_model(p: AllocationProblem):
    m = Model("circulytics-allocation")
    nf = len(p.facilities)
    x, d = {}, {}
    for i, b in enumerate(p.batches):
        for j, f in enumerate(p.facilities):
            if p.eligible(b, f):                 # ineligible pairs are simply not created
                x[i, j] = Binary(f"x_{i}_{j}")
        d[i] = Binary(f"d_{i}")
    m.add_variables(list(x.values()) + list(d.values()))
    m.minimize(quicksum(p.cost(p.batches[i], p.facilities[j]) * v for (i, j), v in x.items())
               + quicksum(p.defer_cost(p.batches[i]) * v for i, v in d.items()))
    for i in range(len(p.batches)):
        m.add_constraint(quicksum(v for (ii, _), v in x.items() if ii == i) + d[i] == 1, label=f"assign_{i}")
    for j, f in enumerate(p.facilities):
        terms = [p.batches[i].mass_kg * v for (i, jj), v in x.items() if jj == j]
        if terms:
            m.add_constraint(quicksum(terms) <= f.capacity_kg, label=f"cap_{j}")
    return m, {"x": {k: v.name for k, v in x.items()}, "d": {k: v.name for k, v in d.items()}}


def decode(p: AllocationProblem, names: dict, solution: dict) -> dict:
    """Turn solver variable values into {batch_id: facility_id | DEFERRED}."""
    from ..classical import DEFER
    out = {}
    for i, b in enumerate(p.batches):
        out[b.id] = DEFER
        hits = [j for (ii, j), nm in names["x"].items() if ii == i and solution.get(nm, 0) > 0.5]
        if len(hits) == 1:
            out[b.id] = p.facilities[hits[0]].id
        elif len(hits) > 1:
            out[b.id] = "INVALID_MULTI:" + ",".join(p.facilities[h].id for h in hits)
    return out
