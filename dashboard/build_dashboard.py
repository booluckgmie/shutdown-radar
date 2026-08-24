"""Render the single self-contained dashboard.html from a data payload.

Takes the dict produced by src/export.py::build_payload and inlines it,
along with the (also self-contained, no build step) app.js, into
dashboard/template.html. The only external dependency left in the output is
the Leaflet CDN bundle + OpenStreetMap tiles — an offline map basemap isn't
practical to inline, so viewing the map layer requires internet; every other
element (filters, charts, tables, insights) works fully offline once the
page has loaded once.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

TEMPLATE_PATH = Path(__file__).parent / "template.html"
APP_JS_PATH = Path(__file__).parent / "app.js"


def _json_for_script(payload: Any) -> str:
    # Safe to embed inside a <script> block: escape "</" so a literal
    # "</script>" inside string data can't terminate the block early.
    return json.dumps(payload, default=str).replace("</", "<\\/")


def render_dashboard(payload: dict[str, Any], out_path: Path) -> None:
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    app_js = APP_JS_PATH.read_text(encoding="utf-8")

    html = template
    html = html.replace("__DATA_JSON__", _json_for_script(payload))
    html = html.replace("__GENERATED_AT__", str(payload.get("generated_at", "")))
    html = html.replace('<script src="app.js.inline"></script>', "<script>\n" + app_js + "\n</script>")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
