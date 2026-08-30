"""Password hashing: work factor, backward compatibility, and upgrade-on-login.

The stored format is ``pbkdf2_sha256$<iterations>$<hex>``. Hashes written
before that format are bare hex at the original 120,000 rounds, so raising the
work factor must never lock an existing user out: their hash still verifies,
and is transparently upgraded the next time they sign in.
"""
from __future__ import annotations

import hashlib

import pytest

from app.core.auth import (
    LEGACY_ITERATIONS,
    hash_password,
    needs_rehash,
    verify_password,
)
from app.core.config import settings

PW = "correct horse battery staple 7"


def _legacy_hash(password: str, salt: str) -> str:
    """Exactly what the pre-versioning code stored: bare hex, 120k rounds."""
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), LEGACY_ITERATIONS
    ).hex()


@pytest.fixture
def iterations():
    """Restore the suite-wide low work factor after tests that change it."""
    original = settings.password_hash_iterations
    yield
    settings.password_hash_iterations = original


# ---------------------------------------------------------------------------
# Format
# ---------------------------------------------------------------------------
def test_hash_records_its_own_work_factor():
    stored, _salt = hash_password(PW, iterations=5_000)
    scheme, iters, digest = stored.split("$")
    assert scheme == "pbkdf2_sha256"
    assert iters == "5000"
    assert len(digest) == 64  # sha256 hex


def test_same_password_hashes_differently_each_time():
    """Distinct salts, so identical passwords don't share a hash."""
    a, salt_a = hash_password(PW)
    b, salt_b = hash_password(PW)
    assert salt_a != salt_b
    assert a != b


def test_round_trip_verifies():
    stored, salt = hash_password(PW)
    assert verify_password(PW, stored, salt) is True


def test_wrong_password_is_rejected():
    stored, salt = hash_password(PW)
    assert verify_password(PW + "x", stored, salt) is False


def test_wrong_salt_is_rejected():
    stored, _salt = hash_password(PW)
    assert verify_password(PW, stored, "0" * 32) is False


def test_empty_password_is_refused():
    with pytest.raises(ValueError):
        hash_password("   ")


@pytest.mark.parametrize("garbage", ["", "not-a-hash", "pbkdf2_sha256$", "$$"])
def test_malformed_stored_hash_never_raises(garbage):
    """A corrupt row must fail closed, not 500 the login endpoint."""
    assert verify_password(PW, garbage, "somesalt") is False


# ---------------------------------------------------------------------------
# Backward compatibility — the part that must not break
# ---------------------------------------------------------------------------
def test_a_legacy_bare_hex_hash_still_verifies():
    salt = "a" * 32
    legacy = _legacy_hash(PW, salt)
    assert "$" not in legacy
    assert verify_password(PW, legacy, salt) is True


def test_a_legacy_hash_rejects_the_wrong_password():
    salt = "a" * 32
    assert verify_password("wrong", _legacy_hash(PW, salt), salt) is False


def test_legacy_hashes_are_flagged_for_upgrade(iterations):
    settings.password_hash_iterations = 600_000
    assert needs_rehash(_legacy_hash(PW, "a" * 32)) is True


def test_a_current_hash_is_not_flagged(iterations):
    settings.password_hash_iterations = 600_000
    stored, _salt = hash_password(PW, iterations=600_000)
    assert needs_rehash(stored) is False


def test_a_weaker_versioned_hash_is_flagged(iterations):
    settings.password_hash_iterations = 600_000
    stored, _salt = hash_password(PW, iterations=150_000)
    assert needs_rehash(stored) is True


def test_raising_the_policy_does_not_invalidate_existing_hashes(iterations):
    """The whole point: the work factor can be raised at any time."""
    settings.password_hash_iterations = 1_000
    stored, salt = hash_password(PW)
    settings.password_hash_iterations = 600_000
    assert verify_password(PW, stored, salt) is True
    assert needs_rehash(stored) is True


# ---------------------------------------------------------------------------
# Upgrade on login
# ---------------------------------------------------------------------------
class TestLoginUpgrade:
    def _make_user(self, db, username: str, password_hash: str, salt: str):
        from app.models.user import User

        u = User(username=username, password_hash=password_hash, password_salt=salt,
                 is_admin=True, is_active=True, preferred_language="en", role="owner")
        db.add(u)
        db.commit()
        return u

    def test_legacy_user_can_log_in_and_is_upgraded(self, client, db, iterations):
        salt = "b" * 32
        user = self._make_user(db, "legacyuser", _legacy_hash(PW, salt), salt)
        settings.password_hash_iterations = LEGACY_ITERATIONS + 10_000

        resp = client.post("/auth/login", json={"username": "legacyuser", "password": PW})
        assert resp.status_code == 200, resp.text

        db.refresh(user)
        assert user.password_hash.startswith(f"pbkdf2_sha256${LEGACY_ITERATIONS + 10_000}$")
        # and the upgraded hash still authenticates the same password
        assert verify_password(PW, user.password_hash, user.password_salt) is True

    def test_a_failed_login_does_not_touch_the_hash(self, client, db, iterations):
        salt = "c" * 32
        legacy = _legacy_hash(PW, salt)
        user = self._make_user(db, "legacyuser2", legacy, salt)
        settings.password_hash_iterations = LEGACY_ITERATIONS + 10_000

        resp = client.post("/auth/login", json={"username": "legacyuser2", "password": "wrong"})
        assert resp.status_code == 401

        db.refresh(user)
        assert user.password_hash == legacy

    def test_an_up_to_date_hash_is_left_alone(self, client, db, iterations):
        settings.password_hash_iterations = 2_000
        stored, salt = hash_password(PW, iterations=2_000)
        user = self._make_user(db, "currentuser", stored, salt)

        assert client.post("/auth/login",
                           json={"username": "currentuser", "password": PW}).status_code == 200
        db.refresh(user)
        assert user.password_hash == stored  # unchanged, no needless write


def test_a_stronger_hash_is_never_downgraded(iterations):
    """Lowering the policy must not mark existing stronger hashes for rehash —
    that would quietly weaken every account on the next login."""
    settings.password_hash_iterations = 50_000
    strong, _salt = hash_password(PW, iterations=600_000)
    assert needs_rehash(strong) is False
    assert needs_rehash(_legacy_hash(PW, "d" * 32)) is False  # 120k > 50k
