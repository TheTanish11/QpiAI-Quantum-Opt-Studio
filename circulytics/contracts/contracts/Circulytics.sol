// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";

/// @title Circulytics
/// @notice Prototype on-chain commitments for EV-battery custody, evidence hashes,
///         material-claim limits and optimisation-run anchoring.
/// @dev    The chain stores identifiers and hashes only. It cannot prove a physical
///         event happened; it proves who committed what, when, and enforces rules
///         (roles, state machine, claim cap) that cannot be bypassed by an off-chain UI.
///         Prototype: not audited. Never use real funds or real confidential data.
contract Circulytics is AccessControl {
    bytes32 public constant MANUFACTURER = keccak256("MANUFACTURER");
    bytes32 public constant COLLECTOR    = keccak256("COLLECTOR");
    bytes32 public constant REPAIRER     = keccak256("REPAIRER");
    bytes32 public constant RECYCLER     = keccak256("RECYCLER");
    bytes32 public constant AUDITOR      = keccak256("AUDITOR");
    bytes32 public constant ATTESTER     = keccak256("ATTESTER"); // e.g. certified weighbridge

    enum State {
        NONE, REGISTERED, IN_SERVICE, RETIRED, COLLECTED, UNDER_ASSESSMENT,
        SECOND_LIFE, RECYCLING_APPROVED, IN_PROCESSING, MATERIALS_RECORDED, RECONCILED
    }

    struct Battery { address manufacturer; address custodian; State state; bytes32 passportRoot; }
    struct Evidence { address issuer; uint64 at; }
    struct Output { address recycler; uint256 massGrams; uint256 claimedGrams; }
    struct Claim { address claimant; bytes32 outputId; uint256 grams; bool active; }

    mapping(bytes32 => Battery) public batteries;
    mapping(bytes32 => address) public pendingTransfer;           // batteryId => proposed receiver
    mapping(bytes32 => Evidence) public evidence;                  // evidence hash => meta
    mapping(bytes32 => Output) public outputs;
    mapping(bytes32 => Claim) public claims;
    mapping(bytes32 => bool) public runAnchored;                   // optimisation record hash

    event BatteryRegistered(bytes32 indexed batteryId, address indexed manufacturer, bytes32 passportRoot);
    event StateAdvanced(bytes32 indexed batteryId, State from, State to, address indexed actor, bytes32 evidenceHash);
    event TransferProposed(bytes32 indexed batteryId, address indexed from, address indexed to);
    event TransferAccepted(bytes32 indexed batteryId, address indexed from, address indexed to);
    event TransferCancelled(bytes32 indexed batteryId);
    event EvidenceAnchored(bytes32 indexed evidenceHash, address indexed issuer);
    event OutputRecorded(bytes32 indexed outputId, address indexed recycler, uint256 massGrams);
    event ClaimCreated(bytes32 indexed claimId, bytes32 indexed outputId, address indexed claimant, uint256 grams);
    event ClaimReleased(bytes32 indexed claimId);
    event RunAnchored(bytes32 indexed recordHash, bytes32 indexed inputHash, string solver);

    constructor(address admin) { _grantRole(DEFAULT_ADMIN_ROLE, admin); }

    // ---------------- helpers ----------------
    function _isParticipant(address a) internal view returns (bool) {
        return hasRole(MANUFACTURER, a) || hasRole(COLLECTOR, a) || hasRole(REPAIRER, a) || hasRole(RECYCLER, a);
    }

    function _validTransition(State f, State t) internal pure returns (bool) {
        if (f == State.REGISTERED) return t == State.IN_SERVICE;
        if (f == State.IN_SERVICE) return t == State.RETIRED;
        if (f == State.RETIRED) return t == State.COLLECTED;
        if (f == State.COLLECTED) return t == State.UNDER_ASSESSMENT;
        if (f == State.UNDER_ASSESSMENT) return t == State.SECOND_LIFE || t == State.RECYCLING_APPROVED;
        if (f == State.RECYCLING_APPROVED) return t == State.IN_PROCESSING;
        if (f == State.IN_PROCESSING) return t == State.MATERIALS_RECORDED;
        if (f == State.MATERIALS_RECORDED) return t == State.RECONCILED;
        return false;
    }

    function _roleAllowed(State t, address a) internal view returns (bool) {
        if (t == State.IN_SERVICE) return hasRole(MANUFACTURER, a);
        if (t == State.RETIRED) return hasRole(MANUFACTURER, a) || hasRole(REPAIRER, a) || hasRole(COLLECTOR, a);
        if (t == State.COLLECTED) return hasRole(COLLECTOR, a);
        if (t == State.UNDER_ASSESSMENT) return hasRole(COLLECTOR, a) || hasRole(REPAIRER, a) || hasRole(RECYCLER, a);
        if (t == State.SECOND_LIFE) return hasRole(REPAIRER, a);
        if (t == State.RECYCLING_APPROVED) return hasRole(RECYCLER, a) || hasRole(REPAIRER, a);
        if (t == State.IN_PROCESSING || t == State.MATERIALS_RECORDED) return hasRole(RECYCLER, a);
        if (t == State.RECONCILED) return hasRole(RECYCLER, a) || hasRole(AUDITOR, a);
        return false;
    }

    function _needsEvidence(State t) internal pure returns (bool) {
        return t == State.UNDER_ASSESSMENT || t == State.SECOND_LIFE
            || t == State.RECYCLING_APPROVED || t == State.IN_PROCESSING;
    }

    // ---------------- passport & lifecycle ----------------
    function registerBattery(bytes32 batteryId, bytes32 passportRoot) external onlyRole(MANUFACTURER) {
        require(batteryId != bytes32(0), "bad id");
        require(batteries[batteryId].state == State.NONE, "already registered");
        batteries[batteryId] = Battery(msg.sender, msg.sender, State.REGISTERED, passportRoot);
        emit BatteryRegistered(batteryId, msg.sender, passportRoot);
    }

    function advanceState(bytes32 batteryId, State to, bytes32 evidenceHash) external {
        Battery storage b = batteries[batteryId];
        require(b.state != State.NONE, "unknown battery");
        require(_validTransition(b.state, to), "invalid transition");
        require(_roleAllowed(to, msg.sender), "role not permitted");
        require(b.custodian == msg.sender || (to == State.RECONCILED && hasRole(AUDITOR, msg.sender)),
                "not custodian");
        if (_needsEvidence(to)) require(evidence[evidenceHash].issuer != address(0), "evidence not anchored");
        State from = b.state;
        b.state = to;
        emit StateAdvanced(batteryId, from, to, msg.sender, evidenceHash);
    }

    // ---------------- custody (propose + explicit accept) ----------------
    function proposeTransfer(bytes32 batteryId, address to) external {
        Battery storage b = batteries[batteryId];
        require(b.state != State.NONE, "unknown battery");
        require(b.custodian == msg.sender, "not custodian");
        require(to != msg.sender && _isParticipant(to), "receiver not authorised");
        require(pendingTransfer[batteryId] == address(0), "transfer pending");
        pendingTransfer[batteryId] = to;
        emit TransferProposed(batteryId, msg.sender, to);
    }

    function acceptTransfer(bytes32 batteryId) external {
        require(pendingTransfer[batteryId] == msg.sender, "not the proposed receiver");
        require(_isParticipant(msg.sender), "authorisation revoked");
        address from = batteries[batteryId].custodian;
        batteries[batteryId].custodian = msg.sender;
        delete pendingTransfer[batteryId];
        emit TransferAccepted(batteryId, from, msg.sender);
    }

    function cancelTransfer(bytes32 batteryId) external {
        require(batteries[batteryId].custodian == msg.sender, "not custodian");
        require(pendingTransfer[batteryId] != address(0), "none pending");
        delete pendingTransfer[batteryId];
        emit TransferCancelled(batteryId);
    }

    // ---------------- evidence commitments ----------------
    function anchorEvidence(bytes32 evidenceHash) external {
        require(_isParticipant(msg.sender) || hasRole(ATTESTER, msg.sender) || hasRole(AUDITOR, msg.sender),
                "not authorised");
        require(evidenceHash != bytes32(0), "empty hash");
        require(evidence[evidenceHash].issuer == address(0), "already anchored");
        evidence[evidenceHash] = Evidence(msg.sender, uint64(block.timestamp));
        emit EvidenceAnchored(evidenceHash, msg.sender);
    }

    // ---------------- material outputs & claims ----------------
    function recordOutput(bytes32 outputId, uint256 massGrams) external onlyRole(RECYCLER) {
        require(massGrams > 0, "zero mass");
        require(outputs[outputId].recycler == address(0), "output exists");
        outputs[outputId] = Output(msg.sender, massGrams, 0);
        emit OutputRecorded(outputId, msg.sender, massGrams);
    }

    function createClaim(bytes32 claimId, bytes32 outputId, uint256 grams) external {
        require(_isParticipant(msg.sender), "not authorised");
        require(claims[claimId].claimant == address(0), "duplicate claim id");
        Output storage o = outputs[outputId];
        require(o.recycler != address(0), "unknown output");
        require(grams > 0 && o.claimedGrams + grams <= o.massGrams, "exceeds unclaimed balance");
        o.claimedGrams += grams;
        claims[claimId] = Claim(msg.sender, outputId, grams, true);
        emit ClaimCreated(claimId, outputId, msg.sender, grams);
    }

    function releaseClaim(bytes32 claimId) external {
        Claim storage c = claims[claimId];
        require(c.claimant == msg.sender, "not claimant");
        require(c.active, "not active");
        c.active = false;
        outputs[c.outputId].claimedGrams -= c.grams;
        emit ClaimReleased(claimId);
    }

    function unclaimed(bytes32 outputId) external view returns (uint256) {
        Output storage o = outputs[outputId];
        return o.massGrams - o.claimedGrams;
    }

    // ---------------- optimisation-run anchoring ----------------
    function anchorRun(bytes32 recordHash, bytes32 inputHash, string calldata solver) external {
        require(hasRole(DEFAULT_ADMIN_ROLE, msg.sender) || hasRole(AUDITOR, msg.sender), "not authorised");
        require(!runAnchored[recordHash], "already anchored");
        runAnchored[recordHash] = true;
        emit RunAnchored(recordHash, inputHash, solver);
    }
}
