"""Display helpers shared by the ISA packaging workspace's views."""
from dash import html

GREEN, AMBER, RED, GRAY, BLUE = "#18974C", "#93510b", "#B42318", "#707372", "#193F90"


def pill(label, color, state):
    return html.Span(label, className="state-pill", style={"--state-color": color}, **{"data-state": state})


def severity_pill(severity):
    label, color = {"error": ("error", RED), "warning": ("warning", AMBER)}.get(severity, (severity or "info", GRAY))
    return pill(label, color, severity or "info")


def mono(text):
    """Machine facts in the shell's monospace, without Bootstrap's coloured <code>."""
    return html.Span(str(text), className="font-monospace")


def clipped(text, limit=240):
    """Long validator messages list every row; show the start and keep the rest on hover."""
    text = str(text or "")
    return text if len(text) <= limit else html.Span(text[:limit].rstrip() + "…", title=text)


def size_label(n_bytes):
    value = float(n_bytes or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1000 or unit == "GB":
            return f"{value:.0f} {unit}" if unit in {"B", "KB"} else f"{value:.1f} {unit}"
        value /= 1000
