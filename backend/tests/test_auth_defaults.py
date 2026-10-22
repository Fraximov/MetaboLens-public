"""Authentication defaults and first-start bootstrap use an isolated SQLite DB."""
import os
import base64
import hashlib
import hmac
import json
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import Settings
from app.core.security import decode_access_token, verify_password
from app.db.base import Base
from app.dependencies import get_current_user
from app.models.models import RoleAssignment, User
from app.services.auth_service import AuthService
from app.services.seed_service import SeedService

TEST_SECRET = "synthetic-test-signing-key-0123456789abcdef"


def settings(**overrides):
    with patch.dict(os.environ, {}, clear=True):
        return Settings(_env_file=None, app_secret_key=TEST_SECRET, **overrides)


@pytest.fixture
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def test_default_settings_disable_demo_auth():
    config = settings()
    assert config.app_env == "production"
    assert config.auto_seed is False
    assert config.testing_god_token is None


@pytest.mark.parametrize("secret", ["", "change-me", "super-secret-key"])
def test_weak_signing_secret_is_rejected(secret):
    with patch.dict(os.environ, {}, clear=True), pytest.raises(ValidationError):
        Settings(_env_file=None, app_env="production", app_secret_key=secret)


@pytest.mark.parametrize("unsafe", [{"auto_seed": True}, {"testing_god_token": "explicit-test-token"}])
def test_production_rejects_demo_auth(unsafe):
    with pytest.raises(ValidationError):
        settings(app_env="production", **unsafe)


def test_default_seed_creates_no_demo_accounts(db):
    with patch("app.services.seed_service.get_settings", return_value=settings(), create=True):
        SeedService(db).run()
    assert db.query(User).count() == 0


def test_bootstrap_creates_admin_once_and_preserves_password(db):
    config = settings(bootstrap_admin_email="owner@example.org", bootstrap_admin_password="first-random-password-for-test")
    with patch("app.services.seed_service.get_settings", return_value=config, create=True):
        SeedService(db).run()
    user = db.query(User).filter_by(email="owner@example.org").one()
    original_hash = user.password_hash
    assert verify_password("first-random-password-for-test", original_hash)
    assert db.query(RoleAssignment).filter_by(user_id=user.id, role="admin", study_id=None).count() == 1
    changed = settings(bootstrap_admin_email="owner@example.org", bootstrap_admin_password="different-random-password-for-test")
    with patch("app.services.seed_service.get_settings", return_value=changed, create=True):
        SeedService(db).run()
    assert db.query(User).count() == 1
    assert user.password_hash == original_hash


@pytest.mark.parametrize("values", [
    {"bootstrap_admin_email": "owner@example.org"},
    {"bootstrap_admin_password": "long-enough-test-password"},
    {"bootstrap_admin_email": "owner@example.org", "bootstrap_admin_password": "short"},
])
def test_bootstrap_requires_email_and_strong_password_together(values):
    with pytest.raises(ValidationError):
        settings(**values)


def test_development_bypass_requires_development_environment(db):
    admin = User(email="admin@example.org", display_name="Synthetic", is_active=True, auth_provider="local")
    db.add(admin)
    db.commit()
    config = settings(app_env="dev", testing_god_token="explicit-test-token", testing_god_user_email=admin.email)
    with patch("app.dependencies.get_settings", return_value=config):
        assert get_current_user("explicit-test-token", db) is admin
        config.app_env = "production"
        with pytest.raises(HTTPException) as rejected:
            get_current_user("explicit-test-token", db)
    assert rejected.value.status_code == 401


def test_bootstrap_password_login_checks_signature_and_active_user(db):
    config = settings(bootstrap_admin_email="owner@example.org", bootstrap_admin_password="random-bootstrap-password-for-test")
    with patch("app.services.seed_service.get_settings", return_value=config):
        SeedService(db).run()
    with patch("app.core.security.get_settings", return_value=config), patch("app.dependencies.get_settings", return_value=config):
        token = AuthService(db).login("owner@example.org", "random-bootstrap-password-for-test")
        user = get_current_user(token, db)
        assert user.email == "owner@example.org"
        header, payload, signature = token.split(".")
        tampered = f"{header}.{payload}.{'A' if signature[0] != 'A' else 'B'}{signature[1:]}"
        with pytest.raises(HTTPException) as rejected:
            get_current_user(tampered, db)
        assert rejected.value.status_code == 401
        user.is_active = False
        db.commit()
        with pytest.raises(HTTPException) as rejected:
            get_current_user(token, db)
        assert rejected.value.status_code == 401


@pytest.mark.parametrize("algorithm,expires", [("none", 4000000000), ("HS512", 4000000000), ("HS256", 1), ("HS256", [])])
def test_invalid_token_algorithms_expiry_and_claims_are_rejected(algorithm, expires):
    def encode(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()
    body = encode({"alg": algorithm, "typ": "JWT"}) + "." + encode({"sub": "owner@example.org", "exp": expires})
    digest = hashlib.sha512 if algorithm == "HS512" else hashlib.sha256
    signature = base64.urlsafe_b64encode(hmac.new(TEST_SECRET.encode(), body.encode(), digest).digest()).rstrip(b"=").decode()
    with patch("app.core.security.get_settings", return_value=settings()):
        assert decode_access_token(body + "." + signature) is None
