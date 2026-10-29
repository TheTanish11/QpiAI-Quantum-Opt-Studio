"""Hashing, canonical JSON, Ed25519 attestations, Merkle roots."""
import hashlib
import json
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives import serialization
from cryptography.exceptions import InvalidSignature


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hash_obj(obj) -> str:
    return sha256_hex(canonical(obj))


def generate_keypair() -> tuple[str, str]:
    """Returns (private_hex, public_hex). Demo/test use only."""
    priv = Ed25519PrivateKey.generate()
    priv_hex = priv.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    ).hex()
    pub_hex = priv.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    ).hex()
    return priv_hex, pub_hex


def sign(private_hex: str, obj) -> str:
    priv = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_hex))
    return priv.sign(canonical(obj)).hex()


def verify(public_hex: str, obj, signature_hex: str) -> bool:
    try:
        pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_hex))
        pub.verify(bytes.fromhex(signature_hex), canonical(obj))
        return True
    except (InvalidSignature, ValueError):
        return False


def merkle_root(leaves: dict) -> str:
    """Merkle root over sorted (key,value) attribute leaves, so a role can be
    given only its leaves plus proofs."""
    level = [hashlib.sha256(canonical([k, v])).digest() for k, v in sorted(leaves.items())]
    if not level:
        return sha256_hex(b"")
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        level = [hashlib.sha256(level[i] + level[i + 1]).digest() for i in range(0, len(level), 2)]
    return level[0].hex()
