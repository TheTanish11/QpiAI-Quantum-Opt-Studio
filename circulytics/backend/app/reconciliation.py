"""Mass-balance reconciliation: D = M_in - (M_recovered + M_residue + M_loss)."""
from dataclasses import dataclass
from decimal import Decimal


class ReconciliationError(ValueError):
    pass


@dataclass(frozen=True)
class ReconResult:
    discrepancy: Decimal
    tolerance: Decimal
    within_tolerance: bool
    status: str  # RECONCILED / FLAGGED


def reconcile(m_in, recovered_by_material: dict, residue, loss, tolerance) -> ReconResult:
    m_in = Decimal(str(m_in))
    residue = Decimal(str(residue))
    loss = Decimal(str(loss))
    tol = Decimal(str(tolerance))
    recovered = sum((Decimal(str(v)) for v in recovered_by_material.values()), Decimal("0"))
    for name, v in [("incoming", m_in), ("residue", residue), ("loss", loss), ("tolerance", tol)]:
        if v < 0:
            raise ReconciliationError(f"negative {name} mass")
    if any(Decimal(str(v)) < 0 for v in recovered_by_material.values()):
        raise ReconciliationError("negative recovered mass")
    if m_in == 0:
        raise ReconciliationError("incoming mass must be positive")
    d = m_in - (recovered + residue + loss)
    ok = abs(d) <= tol
    return ReconResult(d, tol, ok, "RECONCILED" if ok else "FLAGGED")
