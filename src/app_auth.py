from __future__ import annotations

import base64
import hashlib
import hmac
import os

ALGORITHM = 'pbkdf2_sha256'
DEFAULT_ITERATIONS = 600_000


def hash_password(password: str, *, salt: bytes | None = None, iterations: int = DEFAULT_ITERATIONS) -> str:
    if not password:
        raise ValueError('Mật khẩu không được để trống')
    if iterations < 100_000:
        raise ValueError('Số vòng PBKDF2 quá thấp')
    selected_salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), selected_salt, iterations)
    return '$'.join(
        (
            ALGORITHM,
            str(iterations),
            base64.urlsafe_b64encode(selected_salt).decode('ascii'),
            base64.urlsafe_b64encode(digest).decode('ascii'),
        )
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iteration_text, salt_text, expected_text = encoded.split('$', maxsplit=3)
        if algorithm != ALGORITHM:
            return False
        iterations = int(iteration_text)
        salt = base64.urlsafe_b64decode(salt_text.encode('ascii'))
        expected = base64.urlsafe_b64decode(expected_text.encode('ascii'))
    except (ValueError, UnicodeError):
        return False
    actual = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, iterations)
    return hmac.compare_digest(actual, expected)


def verify_credentials(
    submitted_username: str,
    submitted_password: str,
    expected_username: str,
    password_hash: str,
) -> bool:
    username_matches = hmac.compare_digest(submitted_username.encode('utf-8'), expected_username.encode('utf-8'))
    password_matches = verify_password(submitted_password, password_hash)
    return username_matches and password_matches
