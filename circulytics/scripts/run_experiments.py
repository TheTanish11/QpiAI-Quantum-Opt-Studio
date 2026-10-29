"""Run all optimisation experiments and write reproducible result records to docs/results/.
   python scripts/run_experiments.py        (QPIAI_OPT_API_KEY / QPIAI_QCLOUD_API_KEY optional)"""
import json, pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from optimization.problem import synthetic_problem
from optimization import classical
from optimization.validate import validate
from optimization.qpiai_opt import solve as qs
from optimization.qcloud import qaoa

out = ROOT / "docs" / "results"; out.mkdir(parents=True, exist_ok=True)
p = synthetic_problem()
rows = {}
for name, fn in (("greedy", classical.greedy), ("exact", classical.exact)):
    r = fn(p); v = validate(p, r["assignment"])
    rows[name] = {"objective": v["objective"], "feasible": v["feasible"], "runtime_s": round(r["runtime_s"], 5), "deferred": v["deferred"], "loads": v["loads"]}
q = qs.run(p)
rows["qpiai_opt"] = {k: q.get(k) for k in ("status", "reason", "error", "sdk_version", "sdk_status", "sdk_objective", "wall_time_s", "validation")}
gap = (rows["greedy"]["objective"] - rows["exact"]["objective"]) / rows["exact"]["objective"] * 100
rows["greedy_gap_vs_optimum_pct"] = round(gap, 2); rows["input_hash"] = p.input_hash()
(out / "allocation_comparison.json").write_text(json.dumps(rows, indent=2))
qa = qaoa.run_experiment(grid=4, shots=1000)
(out / "qaoa_experiment.json").write_text(json.dumps(qa, indent=2))
print(json.dumps(rows, indent=2)[:1800]); print(json.dumps({k: qa.get(k) for k in ("status", "device", "n_qubits", "feasible_fraction", "matches_classical_optimum")}, indent=2))
