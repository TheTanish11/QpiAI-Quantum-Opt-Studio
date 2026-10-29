# CIRCULYTICS — EV battery passport + material-recovery accountability (prototype)

Combines Q-BITS challenges **WEB3-02** (e-waste recovery claims) and **WEB3-03** (battery passports).
**All data is synthetic.** Prototype auth (`X-Org-Id` header) is for demonstration only.

## What is real vs. not (read before presenting)
| Component | Status |
|---|---|
| Lifecycle state machine, custody propose/accept, role checks, revocation | Implemented + tested (41 backend tests) |
| Ed25519 signed attestations, SHA-256 evidence hashes, hash-chained lifecycle events | Implemented + tested |
| Mass-balance reconciliation (Decimal), claim registry (atomic guarded UPDATE, idempotency, concurrency test) | Implemented + tested |
| Solidity contract (roles, state machine, custody, evidence anchoring, claim cap, run anchoring) | 15 Hardhat tests |
| Backend ↔ chain bridge (`backend/app/chain.py`) | **Partial.** Evidence hashes and accepted optimisation-run records are anchored on-chain via a relayer account and tested against a local Hardhat node. Batteries, custody and claims are **not** yet mirrored on-chain (contract and backend enforce those rules independently). Off by default |
| Classical greedy + exact (HiGHS) allocation, independent validator | Implemented + tested |
| QpiAI Opt | Model is built with the real SDK; the **solve runs only if `QPIAI_OPT_API_KEY` is set**. Otherwise the run is recorded as `NOT_EXECUTED`. Result parsing is written to the SDK's documented `SolveResult` fields but **has not been exercised against a live key** |
| QAOA (QpiAI Quantum SDK) | Real circuits executed on the SDK's `QpiAI-QSV-Local` simulator (no key). Not QCloud unless you set `QPIAI_QCLOUD_API_KEY` and pass a cloud device. No quantum advantage claimed |
| PostgreSQL | Schema is portable; **tested on SQLite only** (no Docker in the build environment) |

## Run it
```bash
# backend (Python 3.11+)
cd backend && python -m venv .venv && source .venv/bin/activate   # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
cd .. && python backend/scripts/seed_demo.py          # builds the demo scenario
cd backend && uvicorn app.main:app --reload            # http://127.0.0.1:8000/docs
# frontend
cd frontend && npm install && npm run dev              # set VITE_API_URL if backend is elsewhere
# tests
python -m pytest backend/tests -q
cd contracts && npm install --legacy-peer-deps && npx hardhat test      # SOLCJS=1 npx hardhat test if solc download is blocked
# chain bridge (optional): terminal 1: cd contracts && npx hardhat node
#   terminal 2: cd contracts && npx hardhat run scripts_deploy.js --network localhost   (prints the address)
#   then: set CIRCULYTICS_ADDRESS=<addr> BLOCKCHAIN_RPC_URL=http://127.0.0.1:8545 RELAYER_PRIVATE_KEY=<hardhat account #1 key>
#   and re-run pytest: the 2 chain tests then execute instead of skipping
# experiments
python scripts/run_experiments.py                      # writes docs/results/*.json
```
Add your keys to `.env` / environment: `QPIAI_OPT_API_KEY` (opt.qpiai.tech), `QPIAI_QCLOUD_API_KEY`.

## Demo story (seeded)
1. 100 kg battery → recycler reports 82 recovered + 15 residue + 2 loss → **1 kg unexplained → FLAGGED**, claims blocked.
2. Clean batch reconciles → 50 kg copper claim accepted → duplicate claim id rejected → over-claim rejected.
3. Attacks (non-manufacturer registers, duplicate serial, skipped state, forged signature, non-custodian transfer) → all rejected and visible in **Audit**.
4. Allocation: greedy (142,961) vs exact optimum (83,602) vs `qpiai-opt` (executes with key).
5. Auditor verifies hash-chain lineage and evidence hashes.

## Honest limits
A hash/signature proves *who recorded what, unchanged*; it cannot prove the battery weighed 100 kg. Passing reconciliation shows consistency, not truth. 100% on our adversarial suite ≠ detection of all real fraud. See `docs/results/`.
