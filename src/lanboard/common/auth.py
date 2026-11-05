"""Room password handling: the password itself never crosses the wire.

* Creating a protected room: the client picks a random salt and sends
  ``verifier = scrypt(password, salt)`` (hex). The server stores only salt + verifier.
* Joining: the server's CHALLENGE carries a fresh random ``nonce``. The client
  derives the verifier from the typed password and the room salt and sends
  ``proof = HMAC-SHA256(verifier, nonce)``. The server recomputes it and compares
  in constant time. The nonce is single use, so a captured proof cannot be replayed.

Limitation (documented): the verifier itself is sent once at room creation, so on
an untrusted LAN that moment should be protected with ``--tls``.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

_SCRYPT = dict(n=2 ** 14, r=8, p=1, dklen=32)


def new_salt() -> str:
    return secrets.token_hex(16)


def new_nonce() -> str:
    return secrets.token_hex(16)


def new_token() -> str:
    return secrets.token_urlsafe(24)


def make_verifier(password: str, salt: str) -> str:
    return hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt), **_SCRYPT).hex()


def make_proof(verifier_hex: str, nonce: str) -> str:
    return hmac.new(bytes.fromhex(verifier_hex), nonce.encode(), hashlib.sha256).hexdigest()


def check_proof(verifier_hex: str, nonce: str, proof: str) -> bool:
    if not isinstance(proof, str):
        return False
    return hmac.compare_digest(make_proof(verifier_hex, nonce), proof)
