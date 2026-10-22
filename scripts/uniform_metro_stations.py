#!/usr/bin/env python3
"""Give every process station of an nf-metro SVG the same pill height.

nf-metro sizes a station's pill to the bundle of lines passing through it, so a
single-line station is a small circle and a station shared with a branch is a
taller pill. The legacy map hid that because every main station carried the
three analysis roads. The MetaboLensR map has one main road, so without this
step its stations come out at mixed heights.

Run after `nf-metro render` (see docs/DEVELOPMENT.md). Each rounded station rect
keeps its centre and takes the tallest pill's height; terminus squares (no rx)
are left alone. The embedded manifest's `h` is updated to match. Running it
twice changes nothing.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

STATION = re.compile(r'<rect [^>]*class="nf-metro-station"[^>]*/>')
MANIFEST = re.compile(r'(<metadata id="diagram-manifest"[^>]*><!\[CDATA\[)(.*?)(\]\]></metadata>)', re.S)


def _attr(tag: str, name: str) -> str | None:
    found = re.search(rf'\b{name}="([^"]*)"', tag)
    return found.group(1) if found else None


def _set(tag: str, name: str, value: float) -> str:
    return re.sub(rf'\b{name}="[^"]*"', f'{name}="{value:.1f}"', tag, count=1)


def uniform(svg: str, height: float | None = None) -> str:
    pills = [m.group(0) for m in STATION.finditer(svg) if _attr(m.group(0), "rx")]
    if not pills:
        return svg
    target = height or max(float(_attr(tag, "height")) for tag in pills)
    resized = {_attr(tag, "data-station-id") for tag in pills}

    def resize(match: re.Match) -> str:
        tag = match.group(0)
        if not _attr(tag, "rx"):
            return tag
        centre = float(_attr(tag, "y")) + float(_attr(tag, "height")) / 2
        return _set(_set(tag, "y", centre - target / 2), "height", target)

    svg = STATION.sub(resize, svg)
    found = MANIFEST.search(svg)
    if found:
        manifest = json.loads(found.group(2))
        for node in manifest.get("nodes") or []:
            if node.get("id") in resized:
                node["h"] = target
        body = json.dumps(manifest, separators=(",", ":"), sort_keys=True)
        svg = svg[:found.start(2)] + body + svg[found.end(2):]
    return svg


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("svg", type=Path)
    parser.add_argument("--height", type=float, default=None,
                        help="pill height in px (default: the tallest pill)")
    args = parser.parse_args()
    args.svg.write_text(uniform(args.svg.read_text(encoding="utf-8"), args.height), encoding="utf-8")


if __name__ == "__main__":
    main()
