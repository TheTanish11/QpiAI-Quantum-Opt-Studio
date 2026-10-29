"""Facility-allocation problem definition, synthetic instance generator, input hashing.

min  sum_ij c_ij x_ij + sum_i p_i d_i
s.t. sum_j x_ij + d_i = 1        (each batch assigned once OR explicitly deferred)
     sum_i m_i x_ij <= C_j       (capacity)
     x_ij = 0 if facility j ineligible for batch i
     x, d binary

d_i is the explicit "defer / reject" option: the model never silently places a batch
in an unsuitable facility just to get a solution.
"""
from __future__ import annotations
import hashlib
import json
import random
from dataclasses import dataclass, asdict, field

DEFER_PENALTY_PER_KG = 100.0   # deferral is allowed but very expensive


@dataclass(frozen=True)
class BatchSpec:
    id: str
    mass_kg: int          # integer kg (synthetic data); keeps QUBO/MILP coefficients exact
    chemistry: str


@dataclass(frozen=True)
class FacilitySpec:
    id: str
    capacity_kg: int
    processing_cost_per_kg: float
    transport_rate_per_kg_km: float
    chemistries: tuple
    licensed: bool = True


@dataclass
class AllocationProblem:
    batches: list
    facilities: list
    distance_km: dict            # distance_km[batch_id][facility_id]
    version: str = "alloc-model-v1"

    def eligible(self, b: BatchSpec, f: FacilitySpec) -> bool:
        return f.licensed and b.chemistry in f.chemistries

    def cost(self, b: BatchSpec, f: FacilitySpec) -> float:
        per_kg = f.processing_cost_per_kg + f.transport_rate_per_kg_km * self.distance_km[b.id][f.id]
        return round(b.mass_kg * per_kg, 4)

    def defer_cost(self, b: BatchSpec) -> float:
        return round(b.mass_kg * DEFER_PENALTY_PER_KG, 4)

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "batches": [asdict(b) for b in self.batches],
            "facilities": [asdict(f) | {"chemistries": list(f.chemistries)} for f in self.facilities],
            "distance_km": self.distance_km,
        }

    def input_hash(self) -> str:
        blob = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(blob).hexdigest()

    @classmethod
    def from_dict(cls, d: dict) -> "AllocationProblem":
        return cls(
            batches=[BatchSpec(**b) for b in d["batches"]],
            facilities=[FacilitySpec(**(f | {"chemistries": tuple(f["chemistries"])})) for f in d["facilities"]],
            distance_km=d["distance_km"], version=d.get("version", "alloc-model-v1"),
        )


def synthetic_problem(n_batches=20, seed=7) -> AllocationProblem:
    """Clearly SYNTHETIC demonstration data (fictional facilities)."""
    rng = random.Random(seed)
    chems = ["NMC", "LFP", "NCA"]
    batches = [BatchSpec(f"B{i+1:02d}", rng.choice([120, 180, 240, 300, 360, 420]), rng.choice(chems))
               for i in range(n_batches)]
    facilities = [
        FacilitySpec("F1-GreenLoop", 1500, 9.0, 0.020, ("NMC", "LFP", "NCA")),
        FacilitySpec("F2-CircuMet", 1300, 7.5, 0.030, ("NMC", "NCA")),
        FacilitySpec("F3-LiRecover", 1400, 8.2, 0.025, ("LFP", "NMC")),
        FacilitySpec("F4-VoltCycle", 900, 6.0, 0.045, ("LFP",)),
    ]
    dist = {b.id: {f.id: rng.randint(40, 600) for f in facilities} for b in batches}
    return AllocationProblem(batches, facilities, dist)
