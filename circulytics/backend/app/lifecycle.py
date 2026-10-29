"""Battery lifecycle state machine.

States are an engineering proposal (not mandated verbatim by the QpiAI brief).
Each transition lists (allowed next states). Extra rules:
  * which organisation roles may perform the transition
  * whether a signed attestation of a given kind is required
"""

ALLOWED_TRANSITIONS = {
    "REGISTERED": {"IN_SERVICE"},
    "IN_SERVICE": {"RETIRED"},
    "RETIRED": {"COLLECTED"},
    "COLLECTED": {"UNDER_ASSESSMENT"},
    "UNDER_ASSESSMENT": {"SECOND_LIFE", "RECYCLING_APPROVED"},
    "SECOND_LIFE": set(),
    "RECYCLING_APPROVED": {"IN_PROCESSING"},
    "IN_PROCESSING": {"MATERIALS_RECORDED"},
    "MATERIALS_RECORDED": {"RECONCILED"},
    "RECONCILED": set(),
}

# roles allowed to drive each target state
TRANSITION_ROLES = {
    "IN_SERVICE": {"MANUFACTURER", "ADMIN"},
    "RETIRED": {"MANUFACTURER", "REPAIRER", "COLLECTOR", "ADMIN"},
    "COLLECTED": {"COLLECTOR", "ADMIN"},
    "UNDER_ASSESSMENT": {"COLLECTOR", "REPAIRER", "RECYCLER", "ADMIN"},
    "SECOND_LIFE": {"REPAIRER", "ADMIN"},
    "RECYCLING_APPROVED": {"RECYCLER", "REPAIRER", "ADMIN"},
    "IN_PROCESSING": {"RECYCLER"},
    "MATERIALS_RECORDED": {"RECYCLER"},
    "RECONCILED": {"RECYCLER", "AUDITOR"},
}

# target states that require a signed attestation of the given kind
REQUIRED_ATTESTATION = {
    "UNDER_ASSESSMENT": "HEALTH_TEST",
    "RECYCLING_APPROVED": "HEALTH_TEST",
    "SECOND_LIFE": "HEALTH_TEST",
    "IN_PROCESSING": "WEIGHBRIDGE",
}


def validate_transition(current_state: str, next_state: str) -> None:
    allowed = ALLOWED_TRANSITIONS.get(current_state)
    if allowed is None:
        raise ValueError(f"Unknown current state: {current_state}")
    if next_state not in allowed:
        raise ValueError(f"Invalid transition: {current_state} -> {next_state}")
