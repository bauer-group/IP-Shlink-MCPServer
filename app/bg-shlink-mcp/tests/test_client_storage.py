"""Tests for the encrypted OAuth client-storage factory.

Two modes to verify (both must always return a wrapped, encrypted store):

* Redis mode  — AUTH_REDIS_URL set → RedisStore + FernetEncryptionWrapper
* Disk mode   — AUTH_REDIS_URL unset → DiskStore + FernetEncryptionWrapper
                (key derived from AUTH_JWT_SIGNING_KEY)

Plus the config-level Fernet-format validator: a malformed
AUTH_STORAGE_ENCRYPTION_KEY must fail at Settings() construction, not deep
inside FastMCP on the first OAuth request.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from key_value.aio.stores.disk import DiskStore
from key_value.aio.stores.redis import RedisStore
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

from auth.client_storage import build_client_storage, _sanitize_redis_url
from config import Settings


# ── Redis mode ──────────────────────────────────────────────────────────────


def test_redis_mode_returns_encrypted_redis_store(monkeypatch, valid_base_env, tmp_path):
    """AUTH_REDIS_URL set + valid Fernet key → RedisStore inside FernetWrapper."""
    monkeypatch.setenv("AUTH_REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("AUTH_STORAGE_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("AUTH_DISK_STORAGE_PATH", str(tmp_path))

    storage = build_client_storage(Settings())

    assert isinstance(storage, FernetEncryptionWrapper)
    # FernetEncryptionWrapper exposes the wrapped store via .key_value
    assert isinstance(storage.key_value, RedisStore)


# ── Disk mode ───────────────────────────────────────────────────────────────


def test_disk_mode_returns_encrypted_disk_store(monkeypatch, valid_base_env, tmp_path):
    """AUTH_REDIS_URL unset → DiskStore at AUTH_DISK_STORAGE_PATH, encrypted."""
    monkeypatch.delenv("AUTH_REDIS_URL", raising=False)
    monkeypatch.setenv("AUTH_DISK_STORAGE_PATH", str(tmp_path / "oauth-state"))

    storage = build_client_storage(Settings())

    assert isinstance(storage, FernetEncryptionWrapper)
    assert isinstance(storage.key_value, DiskStore)
    # The factory must create the directory if missing.
    assert (tmp_path / "oauth-state").is_dir()


async def test_disk_mode_survives_process_restart(monkeypatch, valid_base_env, tmp_path):
    """Same JWT key + same disk path → state written by one factory instance
    is readable by the next. This is the heart of "auth survives container
    restart": OAuth state stays usable across processes.
    """
    monkeypatch.delenv("AUTH_REDIS_URL", raising=False)
    monkeypatch.setenv("AUTH_DISK_STORAGE_PATH", str(tmp_path))

    # First "process" writes something.
    storage_1 = build_client_storage(Settings())
    await storage_1.put("client-x", {"client_id": "abc", "secret": "xyz"})

    # Second "process": fresh Settings + factory call → fresh wrapper.
    storage_2 = build_client_storage(Settings())
    loaded = await storage_2.get("client-x")

    assert loaded == {"client_id": "abc", "secret": "xyz"}


async def test_disk_mode_jwt_key_rotation_invalidates_old_state(
    monkeypatch, valid_base_env, tmp_path
):
    """Rotating AUTH_JWT_SIGNING_KEY MUST make pre-rotation state unreadable.

    Documents the disk-mode security trade-off: JWT key doubles as the
    storage encryption key. Rotating it is intentional invalidation of
    every stored session. Operators wanting independent rotation should
    switch to Redis mode (operator-set AUTH_STORAGE_ENCRYPTION_KEY).
    """
    monkeypatch.delenv("AUTH_REDIS_URL", raising=False)
    monkeypatch.setenv("AUTH_DISK_STORAGE_PATH", str(tmp_path))

    storage_1 = build_client_storage(Settings())
    await storage_1.put("pre-rotation", {"data": "secret"})

    # Rotate the JWT signing key — same 64-char shape as the original
    # but different material.
    monkeypatch.setenv("AUTH_JWT_SIGNING_KEY", "f" * 64)
    storage_2 = build_client_storage(Settings())

    # FernetEncryptionWrapper raises by default when a record cannot be
    # decrypted (raise_on_decryption_error=True). Either an exception OR
    # a None return both prove the old data is unreadable; we accept
    # both shapes so the test stays robust against wrapper config drift.
    try:
        loaded = await storage_2.get("pre-rotation")
    except Exception:
        loaded = "raised"
    assert loaded != {"data": "secret"}


# ── Config-level Fernet validator ───────────────────────────────────────────


def test_malformed_fernet_key_rejected_at_boot(monkeypatch, valid_base_env):
    """A garbage AUTH_STORAGE_ENCRYPTION_KEY must fail Settings(), not runtime."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("AUTH_MODE", "google")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "g-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "g-secret")
    monkeypatch.setenv("AUTH_REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("AUTH_STORAGE_ENCRYPTION_KEY", "not-a-fernet-key")

    with pytest.raises(Exception) as exc:
        Settings()
    assert "Fernet" in str(exc.value)


def test_hex_string_rejected_as_fernet_key(monkeypatch, valid_base_env):
    """Operators occasionally paste a hex string from `openssl rand -hex 32` —
    that's not Fernet-formatted. Reject explicitly."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("AUTH_MODE", "google")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "g-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "g-secret")
    monkeypatch.setenv("AUTH_REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("AUTH_STORAGE_ENCRYPTION_KEY", "a" * 64)  # 64 hex chars

    with pytest.raises(Exception) as exc:
        Settings()
    assert "Fernet" in str(exc.value)


def test_valid_fernet_key_passes_validation(monkeypatch, valid_base_env):
    """The positive path — properly generated Fernet keys must not raise."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("AUTH_MODE", "google")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "g-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "g-secret")
    monkeypatch.setenv("AUTH_REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("AUTH_STORAGE_ENCRYPTION_KEY", Fernet.generate_key().decode())

    Settings()  # must not raise


# ── URL sanitisation (log hygiene) ──────────────────────────────────────────


def test_sanitize_redis_url_strips_password():
    assert _sanitize_redis_url("redis://user:secret@host:6379/0") == "redis://***@host:6379/0"


def test_sanitize_redis_url_passthrough_for_unauthenticated():
    assert _sanitize_redis_url("redis://redis:6379/0") == "redis://redis:6379/0"


def test_sanitize_redis_url_handles_rediss_scheme():
    """TLS variant (rediss://) must be preserved."""
    assert _sanitize_redis_url("rediss://user:pw@host:6380/1") == "rediss://***@host:6380/1"
