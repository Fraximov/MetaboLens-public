from dash import Dash
import dash_bootstrap_components as dbc
from flask_compress import Compress

from callbacks import register_all_callbacks
from pages.shell import app_layout

app = Dash(__name__, external_stylesheets=[dbc.themes.FLATLY], suppress_callback_exceptions=True)
server = app.server

# Figures are the payload here, not the page: the feature map alone is about
# 700 KB of JSON per archive selection and near 110 KB compressed, since its
# customdata repeats a handful of distinct names across thousands of points.
# Flask has no built-in equivalent of Starlette's GZipMiddleware, hence the
# dependency. Callback responses travel over /_dash-update-component, which this
# covers along with Dash's own asset bundles.
Compress(server)
app.layout = app_layout()
register_all_callbacks(app)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8050, debug=False)
