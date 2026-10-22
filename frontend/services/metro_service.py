"""Live state for the workflow metro map.

nf-metro renders the map from `docs/WORKFLOW_METRO_MAP.mmd` and embeds a
manifest in the SVG describing every station:

    <metadata id="diagram-manifest">
      {"match": {"target": "fqProcessName", "type": "regex", "flags": "i"},
       "nodes": [{"id": "msdial5", "label": "MS-DIAL 5", "patterns": [...]}, ...]}

Stations are matched to Nextflow processes by regex against the fully qualified
process name. That is nf-metro's own contract, and this module follows it rather
than inventing one, so adopting their live server later changes nothing here.

nf-metro's live mode needs `nf-metro serve` fed by `--with-weblog` over SSE.
MetaboLens does not: the worker already reads the execution trace and publishes
`process_states` on each job, so the state arrives with the job like any other
field.

Where the patterns live
-----------------------
The manifest is the source of truth. Today every `patterns` list in the rendered
SVG is empty, because the `.mmd` does not declare any, so STATION_PATTERNS below
fills the gap. Declare them in the `.mmd` and re-render, and this override can be
deleted one station at a time -- the manifest always wins.

That is the part that matters when the R pipeline changes: new process names are
a map edit, or one line here, never a change to the reading code.
"""
from __future__ import annotations

import json
from html import escape
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

METRO_SVG = Path(__file__).resolve().parents[1] / "assets" / "metabolens-workflow-metro.svg"
# Drawn from docs/WORKFLOW_METRO_MAP_METABOLENSR.mmd. Its station ids are the
# engine descriptor's stage ids, so a job's stored descriptor colours it.
METABOLENSR_SVG = METRO_SVG.with_name("metabolensr-workflow-metro.svg")
# Drawn from docs/WORKFLOW_METRO_MAP_MASSCUBE.mmd, on the same stage ids.
MASSCUBE_SVG = METRO_SVG.with_name("masscube-workflow-metro.svg")
# Maps and native process requirements, tried in order. Shared stage IDs alone
# do not make an engine-specific illustration valid for another engine.
ENGINE_MAPS = (
    (METABOLENSR_SVG, {'engine': ['peak_detection_msdial5']}),
    (MASSCUBE_SVG, {'engine': ['MASSCUBE_PROCESS']}),
)

# Station id -> regex patterns matched against the fully qualified process name,
# case-insensitively. Only used for stations whose manifest patterns are empty.
STATION_PATTERNS: dict[str, list[str]] = {
    "prepare": [r"prepare_msdial5_input"],
    "msdial5": [r"peak_detection_msdial5", r"adapt_mdalign_to_stats_txt"],
    "is_qc": [r"extract_is_qc", r"build_rt_correction_plan", r"rewrite_mzml_retention_times"],
    "sirius": [r"^RUN_SIRIUS:"],
    # Legacy statpipeline runs only. MetaboLensR runs are drawn on their own map
    # and matched with the patterns in the job's engine descriptor.
    "parse": [r"R_PARSE_MSDIAL"],
    "preprocess": [r"R_PREPROCESS"],
    "normalize": [r"R_NORMALIZE"],
    "statistics": [r"R_STATISTICS"],
    "qc_report": [r"R_QC_REPORT"],
    "final_report": [r"R_FINAL_REPORT"],
    "lipid_summary": [r"R_LIPID_SUMMARY"],
    "feature_export": [r"CANONICALIZE"],
}

# nf-metro's documented vocabulary: pending -> queued -> running -> done, or
# failed. Ordered by which wins when a station covers several processes.
STATE_ORDER = ("failed", "running", "queued", "done", "pending")


@lru_cache(maxsize=4)
def manifest(svg_path: Path = METRO_SVG) -> dict:
    """Parse the diagram manifest embedded in the rendered SVG."""
    svg = _svg_text(svg_path)
    if not svg:
        return {}
    found = re.search(r'<metadata id="diagram-manifest"[^>]*>(.*?)</metadata>', svg, re.S)
    if not found:
        return {}
    raw = re.sub(r"^<!\[CDATA\[|\]\]>$", "", found.group(1).strip()).strip()
    try:
        return json.loads(raw)
    except ValueError:
        return {}


def stations() -> list[dict]:
    """Every station, each with the patterns it should match on."""
    out = []
    for node in manifest().get("nodes") or []:
        station_id = node.get("id")
        if not station_id:
            continue
        patterns = list(node.get("patterns") or []) or STATION_PATTERNS.get(station_id, [])
        out.append({
            "id": station_id,
            "label": node.get("label") or station_id,
            "groups": node.get("groups") or [],
            "patterns": patterns,
        })
    return out


FAILURE_STATUSES = {"ABORTED", "FAILED", "TERMINATED"}
RUNNING_STATUSES = {"RUNNING"}
QUEUED_STATUSES = {"SUBMITTED", "NEW", "PENDING"}
SUCCESS_STATUSES = {"COMPLETED", "CACHED"}


def roll_up(tasks) -> dict[str, str]:
    """One state per process from `(name, status)` pairs.

    Failure outranks running, which outranks done, so a process is only green
    once every one of its tasks finished.
    """
    by_process: dict[str, set[str]] = {}
    for name, status in tasks:
        name = str(name or "").split(" (")[0].strip()
        status = str(status or "").strip().upper()
        if name and name != "-" and status:
            by_process.setdefault(name, set()).add(status)
    states: dict[str, str] = {}
    for name, statuses in by_process.items():
        if statuses & FAILURE_STATUSES:
            states[name] = "failed"
        elif statuses & RUNNING_STATUSES:
            states[name] = "running"
        elif statuses & QUEUED_STATUSES:
            states[name] = "queued"
        elif statuses & SUCCESS_STATUSES:
            states[name] = "done"
    return states


def trace_tasks(trace_text: str | None) -> list[tuple[str, str]]:
    """`(name, status)` for every task in a Nextflow execution_trace.txt.

    Column positions come from the header rather than being assumed, so a
    Nextflow version that reorders them still parses.
    """
    lines = (trace_text or "").splitlines()
    if not lines:
        return []
    header = [h.strip().lower() for h in lines[0].split("\t")]
    try:
        name_at, status_at = header.index("name"), header.index("status")
    except ValueError:
        return []
    tasks = []
    for line in lines[1:]:
        cells = line.split("\t")
        if len(cells) > max(name_at, status_at):
            tasks.append((cells[name_at], cells[status_at]))
    return tasks


def payload_tasks(payload: dict | None) -> list[tuple[str, str]]:
    """`(name, status)` from a job's status payload, for jobs with no trace."""
    return [
        ((task or {}).get("name"), (task or {}).get("status"))
        for task in (payload or {}).get("recent_tasks") or []
    ]


def process_states_from_trace(trace_text: str | None) -> dict[str, str]:
    """Per-process states from a Nextflow execution_trace.txt.

    The complete run. The job's status payload keeps only the last eight tasks,
    which for a full pipeline drops the MS-DIAL stages that ran first.
    """
    return roll_up(trace_tasks(trace_text))


def process_states_from_payload(payload: dict | None) -> dict[str, str]:
    """Per-process states for a job, whichever form its payload is in.

    `process_states` is written by the worker from the full execution trace, but
    only while a job is being polled -- a job that finished before that existed
    will never gain one. Those jobs still carry `recent_tasks`, the last eight
    trace rows in the same {name, status} shape, which is enough to colour the
    map for a run already in the database.
    """
    payload = payload or {}
    states = payload.get("process_states")
    if isinstance(states, dict) and states:
        return {str(k): str(v) for k, v in states.items()}
    return roll_up(
        ((task or {}).get("name"), (task or {}).get("status"))
        for task in payload.get("recent_tasks") or []
    )


def station_states(process_states: dict[str, str] | None) -> dict[str, str]:
    """Map the worker's per-process states onto stations.

    A station takes the most serious state among the processes it matches, so a
    station covering several processes turns red the moment one of them fails
    rather than reporting an average.
    """
    processes = {str(k): str(v) for k, v in (process_states or {}).items()}
    states: dict[str, str] = {}
    for station in stations():
        matched = [
            state
            for name, state in processes.items()
            if any(re.search(p, name, re.IGNORECASE) for p in station["patterns"])
        ]
        if not matched:
            continue
        for candidate in STATE_ORDER:
            if candidate in matched:
                states[station["id"]] = candidate
                break
    return states


def station_snapshot(tasks, process_states: dict[str, str] | None = None, *, station_definitions=None) -> dict[str, dict]:
    """nf-metro's documented snapshot, from task rows and the worker's states.

    Both sources are needed and neither is sufficient. Task rows come from the
    execution trace and carry the counts, but the trace only gains a row once a
    task terminates, so it can never report a task in flight. `process_states`
    is the worker's rollup of the trace *and* the log's `Submitted process >`
    lines, so it is the only source that knows what is running right now.

    The station takes the most serious state either source offers.

        {"msdial5": {"state": "done", "done": 3, "total": 3}, ...}

    Their manifest standard fixes the state vocabulary and this shape, so a later
    swap to `nf-metro serve` replaces where the numbers come from and nothing
    else. The counts are what `station_states` alone could not express.
    """
    rows = [
        (str(name or "").split(" (")[0].strip(), str(status or "").strip().upper())
        for name, status in tasks
    ]
    rows = [(n, s) for n, s in rows if n and n != "-" and s]
    live = {str(k): str(v) for k, v in (process_states or {}).items()}

    snapshot: dict[str, dict] = {}
    for station in stations() if station_definitions is None else station_definitions:
        if not station["patterns"]:
            continue
        matches = lambda name: any(  # noqa: E731
            re.search(p, name, re.IGNORECASE) for p in station["patterns"]
        )
        mine = [status for name, status in rows if matches(name)]
        mine_live = [state for name, state in live.items() if matches(name)]
        if not mine and not mine_live:
            continue

        candidates = list(mine_live)
        if mine:
            statuses = set(mine)
            if statuses & FAILURE_STATUSES:
                candidates.append("failed")
            elif statuses & RUNNING_STATUSES:
                candidates.append("running")
            elif statuses & QUEUED_STATUSES:
                candidates.append("queued")
            elif statuses & SUCCESS_STATUSES:
                candidates.append("done")
        state = next((s for s in STATE_ORDER if s in candidates), "pending")
        snapshot[station["id"]] = {
            "state": state,
            "done": sum(1 for s in mine if s in SUCCESS_STATUSES),
            "total": max(len(mine), len(mine_live)),
        }
    return snapshot


def snapshot_states(snapshot: dict[str, dict] | None) -> dict[str, str]:
    """Just the states, for the styling functions."""
    return {sid: entry.get("state", "") for sid, entry in (snapshot or {}).items()}


# EMBL palette, matching the roads already drawn on the map.
STATE_COLOURS = {
    "done": "#18974C",
    "running": "#193F90",
    "queued": "#B56A00",
    "failed": "#B3322B",
}


@lru_cache(maxsize=4)
def _svg_text(svg_path: Path = METRO_SVG) -> str:
    try:
        return svg_path.read_text(encoding="utf-8")
    except OSError:
        return ""


# Halo width and glow radius per state. A station keeps its white fill and only
# gains a ring, which is nf-metro's own idiom for an overlay and leaves the drawn
# map legible; filling the shape solid erased it.
STATE_HALO = {
    "done": {"ring": 4.0, "glow": 2.5, "alpha": 0.45, "pulse": False},
    "queued": {"ring": 4.0, "glow": 3.0, "alpha": 0.50, "pulse": False},
    "running": {"ring": 6.0, "glow": 6.0, "alpha": 0.70, "pulse": True},
    "failed": {"ring": 5.0, "glow": 4.5, "alpha": 0.60, "pulse": False},
}

# The map paints its own grey ground. Both that rect and the halos behind the
# station labels read --nfm-map-bg, so setting it to the card colour drops the
# panel look while the halos keep masking the lines behind each name.
BASE_CSS = ":root{--nfm-map-bg:#ffffff;}"

PULSE_KEYFRAMES = (
    "@keyframes jwMetroPulse{"
    "0%,100%{stroke-opacity:1}"
    "50%{stroke-opacity:.35}"
    "}"
)


def _rgba(hex_colour: str, alpha: float) -> str:
    h = hex_colour.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


def station_css(station_id: str, state: str) -> str:
    """The rules that give one station its halo.

    `paint-order:stroke` draws the ring behind the fill, so a thick stroke reads
    as a halo around the station rather than swallowing it. The fill is left
    alone, which is what keeps a half-finished map readable.
    """
    colour = STATE_COLOURS.get(state)
    halo = STATE_HALO.get(state)
    if not colour or not halo:
        return ""
    # rect only: the station label is a <text> carrying the same
    # data-station-id, so an unqualified selector haloed the name too.
    sel = f'rect[data-station-id="{station_id}"]'
    rules = (
        f"{sel}{{"
        f"stroke:{colour};"
        f"stroke-width:{halo['ring']};"
        f"paint-order:stroke;"
        f"filter:drop-shadow(0 0 {halo['glow']}px {_rgba(colour, halo['alpha'])});"
        f"}}"
    )
    if halo["pulse"]:
        rules += f"{sel}{{animation:jwMetroPulse 1.8s ease-in-out infinite;}}"
    return rules


def styled_svg(states: dict[str, str] | None, svg_path: Path = METRO_SVG) -> str:
    """The map with each station's state drawn as a halo.

    Every station rect carries `data-station-id`, so a state is pure CSS and the
    drawing is never rewritten. A station with no state keeps the map's own
    styling.
    """
    svg = _svg_text(svg_path)
    if not svg:
        return ""
    rules = BASE_CSS + "".join(
        station_css(station_id, state) for station_id, state in (states or {}).items()
    )
    css = rules
    if "jwMetroPulse" in rules:
        # Held to a still ring where motion is unwelcome; the colour already
        # carries the state, so nothing is lost.
        css = (PULSE_KEYFRAMES + rules
               + "@media(prefers-reduced-motion:reduce){"
                 "[data-station-id]{animation:none!important}}")
    return svg.replace("</svg>", f"<style>{css}</style></svg>", 1)


def data_uri(states: dict[str, str] | None, svg_path: Path = METRO_SVG) -> str:
    """The styled map as an image source.

    Kept as an <img> rather than inlined into the DOM: the states are baked in
    server-side, so nothing on the page needs to reach inside the drawing.
    """
    svg = styled_svg(states, svg_path)
    return "data:image/svg+xml;utf8," + quote(svg) if svg else ""


def unmatched_processes(process_states: dict[str, str] | None, *, station_definitions=None) -> list[str]:
    """Processes no station claims.

    Surfacing these is how a renamed process is noticed: after the R pipeline is
    replaced, its new names appear here until a pattern covers them.
    """
    patterns = [p for station in (stations() if station_definitions is None else station_definitions) for p in station["patterns"]]
    return sorted(
        name
        for name in (process_states or {})
        if not any(re.search(p, str(name), re.IGNORECASE) for p in patterns)
    )


def engine_map(stages: list[dict] | None) -> tuple[Path, list[dict]] | None:
    """The drawn map for a descriptor-backed run, with its stations' patterns.

    A map fits when it draws every declared stage and its process requirements
    match the descriptor. The job's
    stored descriptor supplies the patterns, so a run is always matched against
    the process names it actually ran; stations the descriptor does not name
    (inputs, review pauses) keep the manifest's own, usually none. Returns None
    when no map fits, and the caller falls back to the plain stage list.
    """
    declared = {s.get("id"): s for s in stages or [] if s.get("id")}
    if not declared:
        return None
    for svg_path, requirements in ENGINE_MAPS:
        if any(not set(patterns) <= set(declared.get(stage, {}).get('patterns', []))
               for stage, patterns in requirements.items()):
            continue
        nodes = [n for n in manifest(svg_path).get("nodes") or [] if n.get("id")]
        if not set(declared) <= {n["id"] for n in nodes}:
            continue
        return svg_path, [
            {
                "id": node["id"],
                "label": node.get("label") or node["id"],
                "groups": node.get("groups") or [],
                "patterns": list((declared.get(node["id"]) or {}).get("patterns")
                                 or node.get("patterns") or []),
            }
            for node in nodes
        ]
    return None


def engine_stage_data_uri(stages: list[dict], snapshot: dict | None = None) -> str:
    """Render declared stages without assuming a particular engine or graph shape."""
    snapshot = snapshot or {}
    height = max(1, len(stages)) * 44 + 16
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 740 {height}" role="img">',
           '<rect width="100%" height="100%" fill="white"/>',
           '<g font-family="Arial, sans-serif" font-size="14" fill="#253333">']
    for i, stage in enumerate(stages):
        y = 30 + i * 44
        state = snapshot.get(stage['id'], {}).get('state', 'pending')
        colour = STATE_COLOURS.get(state, '#879797')
        svg.extend([
            f'<circle cx="18" cy="{y - 5}" r="6" fill="{colour}"/>',
            f'<text x="36" y="{y}">{escape(stage["label"])}</text>',
            f'<text x="640" y="{y}" fill="{colour}">{escape(state)}</text>',
        ])
    return 'data:image/svg+xml;charset=utf-8,' + quote(''.join([*svg, '</g></svg>']))
