import dash
from dash import Input, Output, State

from services import auth_service
from services.api_client import APIError


def register_callbacks(app):
    @app.callback(
        Output("token-store", "data"),
        Output("login-status", "children"),
        Input("login-btn", "n_clicks"),
        State("login-email", "value"),
        State("login-password", "value"),
        prevent_initial_call=True,
    )
    def do_login(_, email, password):
        try:
            return auth_service.login(email, password), "Login OK"
        except APIError as exc:
            return dash.no_update, f"Login failed: {exc}"
