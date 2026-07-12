from app_auth import hash_password, verify_credentials, verify_password


def test_password_hash_roundtrip_and_rejects_wrong_password() -> None:
    encoded = hash_password('correct horse battery staple', salt=b'0123456789abcdef', iterations=100_000)

    assert verify_password('correct horse battery staple', encoded)
    assert not verify_password('wrong password', encoded)
    assert 'correct horse battery staple' not in encoded


def test_password_verification_fails_closed_for_invalid_hash() -> None:
    assert not verify_password('anything', 'invalid')
    assert not verify_password('anything', 'unknown$100000$c2FsdA==$ZGlnZXN0')


def test_credentials_require_both_username_and_password() -> None:
    encoded = hash_password('secret', salt=b'0123456789abcdef', iterations=100_000)

    assert verify_credentials('minh', 'secret', 'minh', encoded)
    assert not verify_credentials('other', 'secret', 'minh', encoded)
    assert not verify_credentials('minh', 'wrong', 'minh', encoded)
