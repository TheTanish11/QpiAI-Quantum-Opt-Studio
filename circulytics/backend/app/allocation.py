"""Glue between the API and the optimization package. Every run is validated
independently and stored with a hash-sealed record."""
import sys, pathlib
ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy.orm import Session
from optimization.problem import AllocationProblem
from optimization import classical
from optimization.validate import validate
from optimization.qpiai_opt import solve as qpiai_solve
from . import crypto
from .models import AllocationRun
from .services import audit, Rejected
from .chain import get_bridge


def solve_allocation(db: Session, actor_id: int, problem_dict: dict, solver: str) -> AllocationRun:
    p = AllocationProblem.from_dict(problem_dict)
    if solver == "classical-greedy":
        raw = classical.greedy(p)
    elif solver == "classical-exact":
        raw = classical.exact(p)
    elif solver == "qpiai-opt":
        raw = qpiai_solve.run(p)
        if raw["status"] != "EXECUTED":
            # no fabricated result: record the attempt and refuse
            rec = {"solver": solver, "raw": raw}
            run = AllocationRun(solver=solver, input_hash=p.input_hash(), status=raw["status"], objective=None,
                                record=rec, record_hash=crypto.hash_obj(rec))
            db.add(run); audit(db, actor_id, "ALLOCATE", solver, "REJECTED", raw.get("reason") or raw.get("error", ""))
            db.commit()
            return run
        raw = {"solver": solver, "assignment": raw["assignment"], "runtime_s": raw["wall_time_s"], "sdk": raw}
    else:
        raise Rejected("unknown solver", 422)
    val = validate(p, raw["assignment"])
    status = "ACCEPTED" if val["feasible"] else "REJECTED"
    rec = {"solver": solver, "assignment": raw["assignment"], "runtime_s": raw["runtime_s"],
           "validation": val, "input_hash": p.input_hash(), "sdk": raw.get("sdk")}
    run = AllocationRun(solver=solver, input_hash=p.input_hash(), status=status,
                        objective=val["objective"] if val["feasible"] else None,
                        record=rec, record_hash=crypto.hash_obj(rec))
    db.add(run)
    audit(db, actor_id, "ALLOCATE", solver, "OK" if val["feasible"] else "REJECTED", "; ".join(val["errors"]))
    db.commit()
    if val["feasible"]:  # only accepted runs are anchored
        anchor = get_bridge().anchor_run(run.record_hash, run.input_hash, solver)
        if anchor is not None:
            run.record = {**run.record, "chain_anchor": anchor}   # NOTE: record_hash covers the record WITHOUT chain_anchor
            db.commit()
    return run
