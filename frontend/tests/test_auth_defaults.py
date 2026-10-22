from dash import Input

from callbacks.auth import register_callbacks
from pages.shell import app_layout
from tests.test_cache_invalidation import _CallbackCapture, _walk


def test_login_has_no_demo_credentials_or_token_button():
    components = {getattr(item, "id", None): item for item in _walk(app_layout()) if getattr(item, "id", None)}
    assert "login-test-token-btn" not in components
    assert not getattr(components["login-email"], "value", None)
    assert not getattr(components["login-password"], "value", None)


def test_no_callback_returns_a_test_token():
    app = _CallbackCapture()
    register_callbacks(app)
    input_ids = {item.component_id for dependencies in app.dependencies for item in dependencies if isinstance(item, Input)}
    assert input_ids == {"login-btn"}
