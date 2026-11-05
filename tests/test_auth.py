from lanboard.common import auth


def test_challenge_response():
    salt = auth.new_salt()
    verifier = auth.make_verifier("hunter2", salt)
    nonce = auth.new_nonce()
    proof = auth.make_proof(auth.make_verifier("hunter2", salt), nonce)
    assert auth.check_proof(verifier, nonce, proof)
    assert not auth.check_proof(verifier, auth.new_nonce(), proof)       # replay with new nonce
    assert not auth.check_proof(verifier, nonce, auth.make_proof(auth.make_verifier("wrong", salt), nonce))
