from src.core.security import (
    create_access_token,
    decode_access_token,
    get_password_hash,
    verify_password,
)


def test_password_hash_round_trip() -> None:
    password = "synthetic-test-password"
    password_hash = get_password_hash(password)

    assert password_hash != password
    assert verify_password(password, password_hash)
    assert not verify_password("different-password", password_hash)


def test_access_token_contains_subject_and_expiry() -> None:
    token = create_access_token({"sub": "123", "sid": "synthetic-session"})
    payload = decode_access_token(token)

    assert payload["sub"] == "123"
    assert payload["sid"] == "synthetic-session"
    assert "exp" in payload
