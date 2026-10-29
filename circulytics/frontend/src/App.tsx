import { useCallback, useEffect, useState } from "react";
import { api, API } from "./api";

type Row = Record<string, any>;
const Pill = ({ v }: { v: string }) => <span className={`pill ${v}`}>{v}</span>;

function Table({ rows, cols }: { rows: Row[]; cols: string[] }) {
  if (!rows.length) return <p className="mute">No records.</p>;
  return (
    <table>
      <thead><tr>{cols.map(c => <th key={c}>{c.replace(/_/g, " ")}</th>)}</tr></thead>
      <tbody>{rows.map((r, i) => (
        <tr key={r.id ?? i}>{cols.map(c => {
          const v = r[c];
          const isStatus = ["status", "outcome"].includes(c);
          return <td key={c} className={String(c).includes("hash") ? "mono" : ""}>{isStatus ? <Pill v={String(v)} /> : String(v ?? "—")}</td>;
        })}</tr>))}
      </tbody>
    </table>
  );
}

const TABS = ["Dashboard", "Battery passports", "Custody", "Reconciliation", "Claims", "Facility optimizer", "Audit"] as const;

export default function App() {
  const [tab, setTab] = useState<(typeof TABS)[number]>("Dashboard");
  const [orgs, setOrgs] = useState<Row[]>([]);
  const [acting, setActing] = useState<number>(0);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [tick, setTick] = useState(0);
  const refresh = () => setTick(t => t + 1);

  useEffect(() => {
    api("/organizations").then((o: Row[]) => { setOrgs(o); setActing(a => a || o[0]?.id || 0); })
      .catch(e => setErr(`Cannot reach API at ${API}. Start the backend (see README). ${e.message}`));
  }, [tick]);

  const act = useCallback(async (fn: () => Promise<any>, ok: string) => {
    setMsg(""); setErr("");
    try { await fn(); setMsg(ok); refresh(); } catch (e: any) { setErr(`Rejected by backend — ${e.message}`); refresh(); }
  }, []);

  return (
    <>
      <header>
        <h1>CIRC<span>ULYTICS</span> <span className="tag">SYNTHETIC DEMO DATA</span></h1>
        <label className="mute">Acting as&nbsp;
          <select value={acting} onChange={e => setActing(+e.target.value)}>
            {orgs.map(o => <option key={o.id} value={o.id}>{o.name} · {o.role}{o.active ? "" : " (revoked)"}</option>)}
          </select>
        </label>
      </header>
      <nav>{TABS.map(t => <button key={t} className={t === tab ? "on" : ""} onClick={() => setTab(t)}>{t}</button>)}</nav>
      <main>
        {msg && <p className="OK" style={{ border: 0 }}>{msg}</p>}
        {err && <p className="err">{err}</p>}
        {tab === "Dashboard" && <Dashboard tick={tick} />}
        {tab === "Battery passports" && <Passports tick={tick} />}
        {tab === "Custody" && <Custody tick={tick} acting={acting} act={act} />}
        {tab === "Reconciliation" && <Recon tick={tick} />}
        {tab === "Claims" && <Claims tick={tick} acting={acting} act={act} />}
        {tab === "Facility optimizer" && <Optimizer tick={tick} acting={acting} act={act} />}
        {tab === "Audit" && <Audit tick={tick} />}
      </main>
    </>
  );
}

function useGet<T = any>(path: string, tick: number, init: T) {
  const [d, setD] = useState<T>(init);
  useEffect(() => { api<T>(path).then(setD).catch(() => {}); }, [path, tick]);
  return d;
}

function Dashboard({ tick }: { tick: number }) {
  const d = useGet<Row>("/dashboard", tick, {});
  const kp: [string, string][] = [["batteries", "Registered batteries"], ["awaiting_assessment", "Awaiting assessment"],
    ["processing_batches", "Processing batches"], ["reconciliation_exceptions", "Reconciliation exceptions"],
    ["active_claims", "Active claims"], ["duplicate_or_excess_claim_attempts", "Duplicate / excess claim attempts"],
    ["rejected_attempts", "Rejected attempts (all)"], ["allocation_runs", "Allocation runs"]];
  return (
    <>
      <p className="mute">{d.data_label}. A recorded quantity is a <i>claim</i>; it becomes <i>attested</i> only when signed by an authorised party, and <i>physically verified</i> only by inspection or audit. This dashboard shows the first three levels.</p>
      <div className="grid">{kp.map(([k, l]) => <div key={k} className="card kpi"><b>{d[k] ?? "–"}</b><span>{l}</span></div>)}</div>
    </>
  );
}

function Passports({ tick }: { tick: number }) {
  const bats = useGet<Row[]>("/batteries", tick, []);
  const [sel, setSel] = useState<number | null>(null);
  const hist = useGet<Row>(sel ? `/batteries/${sel}/history` : "/", tick, {});
  const atts = useGet<Row[]>("/attestations", tick, []);
  return (
    <>
      <div className="card"><h2>Battery passports</h2>
        <Table rows={bats.map(b => ({ ...b, passport_root: b.passport_root?.slice(0, 16) + "…" }))} cols={["id", "serial_number", "chemistry", "mass_kg", "state", "custodian_id", "processed", "passport_root"]} />
        <p>Inspect: <select value={sel ?? ""} onChange={e => setSel(e.target.value ? +e.target.value : null)}><option value="">select battery…</option>
          {bats.map(b => <option key={b.id} value={b.id}>{b.serial_number}</option>)}</select></p></div>
      {sel && hist.events && <div className="card"><h2>Lifecycle history · hash-chain {hist.lineage?.continuous ? <Pill v="OK" /> : <Pill v="FAILED" />}</h2>
        <Table rows={hist.events.map((e: Row) => ({ ...e, event_hash: e.event_hash.slice(0, 16) + "…" }))} cols={["id", "from_state", "to_state", "actor_id", "attestation_id", "event_hash"]} /></div>}
      <div className="card"><h2>Signed attestations</h2>
        <Table rows={atts.map(a => ({ ...a, payload_hash: a.payload_hash.slice(0, 16) + "…" }))} cols={["id", "kind", "subject_id", "issuer_id", "payload_hash"]} /></div>
    </>
  );
}

function Custody({ tick, acting, act }: { tick: number; acting: number; act: any }) {
  const t = useGet<Row[]>("/custody-transfers", tick, []);
  return (
    <div className="card"><h2>Custody transfers</h2>
      <p className="mute">A handover is only effective after the receiving organisation explicitly accepts it.</p>
      <Table rows={t} cols={["id", "battery_id", "sender_id", "receiver_id", "status"]} />
      {t.filter(x => x.status === "PROPOSED").map(x => (
        <p key={x.id}>Transfer #{x.id} pending →&nbsp;
          <button className="act" onClick={() => act(() => api(`/custody-transfers/${x.id}/accept`, { method: "POST", org: acting }), `Transfer ${x.id} accepted`)}>Accept as selected org</button></p>))}
    </div>
  );
}

function Recon({ tick }: { tick: number }) {
  const b = useGet<Row[]>("/processing-batches", tick, []);
  return <div className="card"><h2>Mass-balance reconciliation</h2>
    <p className="mute">D = M<sub>in</sub> − (M<sub>recovered</sub> + M<sub>residue</sub> + M<sub>loss</sub>); batch passes when |D| ≤ tolerance. Passing shows the records are consistent, not that the measurements are true.</p>
    <Table rows={b} cols={["id", "facility_id", "incoming_mass_kg", "residue_kg", "documented_loss_kg", "discrepancy_kg", "tolerance_kg", "status"]} /></div>;
}

function Claims({ tick, acting, act }: { tick: number; acting: number; act: any }) {
  const out = useGet<Row[]>("/material-outputs", tick, []);
  const claims = useGet<Row[]>("/material-claims", tick, []);
  const [o, setO] = useState(0); const [q, setQ] = useState("5"); const [k, setK] = useState("CLM-" + Math.floor(Math.random() * 9999));
  return (
    <>
      <div className="card"><h2>Material outputs & claim balance</h2>
        <Table rows={out} cols={["id", "batch_id", "material_type", "mass_kg", "claimed_kg", "remaining_kg"]} /></div>
      <div className="card"><h2>Submit a claim (as selected org)</h2>
        <select value={o} onChange={e => setO(+e.target.value)}><option value={0}>output…</option>{out.map(x => <option key={x.id} value={x.id}>#{x.id} {x.material_type} (left {x.remaining_kg})</option>)}</select>{" "}
        <input value={q} onChange={e => setQ(e.target.value)} size={6} /> kg {" "}
        <input value={k} onChange={e => setK(e.target.value)} size={14} />{" "}
        <button className="act" onClick={() => act(() => api("/material-claims", { method: "POST", org: acting, body: { claim_key: k, output_id: o, quantity_kg: q } }), "Claim accepted")}>Claim</button>
        <p className="mute">Try an amount larger than the balance, or reuse a claim id: the backend rejects it.</p></div>
      <div className="card"><h2>Claims</h2><Table rows={claims} cols={["id", "claim_key", "output_id", "claimant_id", "quantity_kg", "status"]} /></div>
    </>
  );
}

function Optimizer({ tick, acting, act }: { tick: number; acting: number; act: any }) {
  const runs = useGet<Row[]>("/allocations", tick, []);
  const [open, setOpen] = useState<Row | null>(null);
  return (
    <>
      <div className="card"><h2>Facility allocation (20 synthetic batches · 4 facilities)</h2>
        {["classical-greedy", "classical-exact", "qpiai-opt"].map(s => (
          <button key={s} className="act" style={{ marginRight: 8 }} onClick={() => act(() => api("/allocations/solve", { method: "POST", org: acting, body: { solver: s } }), `${s} run recorded`)}>Run {s}</button>))}
        <p className="mute">Every result is independently re-validated for eligibility and capacity. <b>qpiai-opt</b> reports NOT_EXECUTED unless a QpiAI Opt API key is configured on the backend — no result is fabricated.</p></div>
      <div className="card"><h2>Optimization run records</h2>
        <Table rows={runs.map(r => ({ id: r.id, solver: r.solver, status: r.status, objective: r.objective, input_hash: r.input_hash.slice(0, 14) + "…", record_hash: r.record_hash.slice(0, 14) + "…" }))} cols={["id", "solver", "status", "objective", "input_hash", "record_hash"]} />
        <p>Inspect: <select onChange={e => setOpen(runs.find(r => r.id === +e.target.value) ?? null)}><option value="">select run…</option>{runs.map(r => <option key={r.id} value={r.id}>#{r.id} {r.solver}</option>)}</select></p>
        {open && <pre className="mono" style={{ maxHeight: 300, overflow: "auto" }}>{JSON.stringify(open.record.validation ?? open.record, null, 2)}</pre>}</div>
    </>
  );
}

function Audit({ tick }: { tick: number }) {
  const ev = useGet<Row[]>("/audit-events", tick, []);
  return <div className="card"><h2>Audit trail (newest first, includes rejected attempts)</h2>
    <Table rows={ev} cols={["id", "at", "actor_id", "action", "subject", "outcome", "detail"]} /></div>;
}
