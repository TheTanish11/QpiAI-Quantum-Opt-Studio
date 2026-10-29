"""Independent validation of ANY proposed allocation. Never trust a solver's own report."""
from __future__ import annotations
from .problem import AllocationProblem
from .classical import DEFER


def validate(p: AllocationProblem, assignment: dict) -> dict:
    errors = []
    bmap = {b.id: b for b in p.batches}
    fmap = {f.id: f for f in p.facilities}
    load = {f.id: 0 for f in p.facilities}
    objective = 0.0
    missing = [b for b in bmap if b not in assignment]
    extra = [b for b in assignment if b not in bmap]
    if missing:
        errors.append(f"unassigned batches (not even deferred): {missing}")
    if extra:
        errors.append(f"unknown batches in result: {extra}")
    deferred = []
    for bid, fid in assignment.items():
        if bid not in bmap:
            continue
        b = bmap[bid]
        if fid == DEFER:
            deferred.append(bid)
            objective += p.defer_cost(b)
            continue
        f = fmap.get(fid)
        if f is None:
            errors.append(f"{bid}: unknown facility {fid}")
            continue
        if not p.eligible(b, f):
            errors.append(f"{bid}: facility {fid} is not eligible (licence/chemistry)")
        load[fid] += b.mass_kg
        objective += p.cost(b, f)
    for fid, l in load.items():
        if l > fmap[fid].capacity_kg:
            errors.append(f"{fid}: capacity exceeded ({l} > {fmap[fid].capacity_kg})")
    return {"feasible": not errors, "errors": errors, "objective": round(objective, 4),
            "loads": load, "deferred": deferred}
