"""Optional bridge: anchors hashes to the Circulytics contract through a relayer account.

Configure with env vars:
  BLOCKCHAIN_RPC_URL, CIRCULYTICS_ADDRESS, RELAYER_PRIVATE_KEY (test key only!)
The relayer account needs ATTESTER (to anchor evidence) and AUDITOR (to anchor runs) roles.
If not configured, every call returns None and the business operation proceeds; a failed
chain write is NEVER reported as confirmed (status is only 'CONFIRMED' with a mined, successful receipt).
"""
import json, os, pathlib

ART = pathlib.Path(__file__).resolve().parents[2] / "contracts" / "artifacts" / "contracts" / "Circulytics.sol" / "Circulytics.json"


def _b32(hex_or_text: str) -> bytes:
    h = hex_or_text.removeprefix("0x")
    if len(h) == 64:
        return bytes.fromhex(h)
    from web3 import Web3
    return bytes(Web3.keccak(text=hex_or_text))


class Bridge:
    def __init__(self):
        self.w3 = self.contract = self.acct = None
        url, addr, key = (os.getenv("BLOCKCHAIN_RPC_URL"), os.getenv("CIRCULYTICS_ADDRESS"),
                          os.getenv("RELAYER_PRIVATE_KEY"))
        if not (url and addr and key and ART.exists()):
            return
        from web3 import Web3
        self.w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 10}))
        self.acct = self.w3.eth.account.from_key(key)
        self.contract = self.w3.eth.contract(address=Web3.to_checksum_address(addr),
                                             abi=json.loads(ART.read_text())["abi"])

    @property
    def enabled(self) -> bool:
        return self.contract is not None

    def _send(self, fn) -> dict:
        try:
            tx = fn.build_transaction({"from": self.acct.address,
                                       "nonce": self.w3.eth.get_transaction_count(self.acct.address)})
            signed = self.acct.sign_transaction(tx)
            h = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            r = self.w3.eth.wait_for_transaction_receipt(h, timeout=30)
            ok = r["status"] == 1
            return {"status": "CONFIRMED" if ok else "REVERTED", "tx_hash": h.hex(), "block": r["blockNumber"]}
        except Exception as e:  # revert, RPC down, etc. -> not confirmed
            return {"status": "FAILED", "error": str(e)[:200]}

    def anchor_evidence(self, evidence_hash: str):
        if not self.enabled:
            return None
        return self._send(self.contract.functions.anchorEvidence(_b32(evidence_hash)))

    def anchor_run(self, record_hash: str, input_hash: str, solver: str):
        if not self.enabled:
            return None
        return self._send(self.contract.functions.anchorRun(_b32(record_hash), _b32(input_hash), solver))

    def run_is_anchored(self, record_hash: str) -> bool:
        return bool(self.enabled and self.contract.functions.runAnchored(_b32(record_hash)).call())

    def evidence_is_anchored(self, evidence_hash: str) -> bool:
        return bool(self.enabled and self.contract.functions.evidence(_b32(evidence_hash)).call()[0]
                    != "0x0000000000000000000000000000000000000000")


def get_bridge() -> Bridge:
    return Bridge()   # cheap; re-reads env so tests can configure it
