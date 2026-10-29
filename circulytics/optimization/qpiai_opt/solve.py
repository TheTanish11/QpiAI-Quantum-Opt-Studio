"""Run the allocation on QpiAI Opt and return an honest, reproducible run record.

Status values (never fabricated):
  EXECUTED      - solve() returned a result (record contains the SDK's real fields)
  NOT_EXECUTED  - no QPIAI_OPT_API_KEY configured; model was built/compiled only
  FAILED        - SDK raised; the error is preserved
"""
from __future__ import annotations
import datetime as dt
import os
import platform
import time
from importlib.metadata import version

from ..problem import AllocationProblem
from ..validate import validate
from .formulate import build_model, decode


def _sdk_version():
    try:
        return version("qpiai-opt")
    except Exception:
        return "unknown"


def run(p: AllocationProblem, mode: str = "local", api_key: str | None = None) -> dict:
    api_key = api_key or os.getenv("QPIAI_OPT_API_KEY")
    model, names = build_model(p)
    rec = {
        "solver": "qpiai-opt", "sdk_version": _sdk_version(), "python": platform.python_version(),
        "model_version": p.version, "input_hash": p.input_hash(), "mode": mode,
        "started_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "model_summary": model.summary(),
    }
    if not api_key:
        rec.update(status="NOT_EXECUTED",
                   reason="QPIAI_OPT_API_KEY not set: the model was built but no solve was submitted",
                   assignment=None, validation=None)
        return rec
    from qpiai_opt.opt_solvers import solve, Client
    t0 = time.perf_counter()
    try:
        client = Client(api_key=api_key)
        res = solve(model, mode=mode, client=client)
        elapsed = time.perf_counter() - t0
        sol = getattr(res, "solution", None) or {}
        assignment = decode(p, names, sol)
        rec.update(
            status="EXECUTED", wall_time_s=round(elapsed, 4),
            sdk_status=getattr(res, "status", None), sdk_model_status=getattr(res, "model_status", None),
            sdk_objective=getattr(res, "objective", None), sdk_execution_mode=getattr(res, "execution_mode", None),
            assignment=assignment, validation=validate(p, assignment),
        )
    except Exception as e:                         # preserve error; do NOT fabricate a result
        rec.update(status="FAILED", error=f"{type(e).__name__}: {e}", assignment=None, validation=None)
    return rec
