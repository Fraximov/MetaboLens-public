"""Guard for callbacks that change data when a button is clicked."""
import dash


def real_click() -> bool:
    """True only when the triggering button was actually clicked.

    Rendering a page can fire a button callback with n_clicks=0 even with
    prevent_initial_call=True. On the launch page that reported a failed launch
    before anything was clicked; a callback that changes data must not act on it.
    """
    triggered = dash.ctx.triggered
    return bool(triggered) and (triggered[0].get("value") or 0) >= 1
