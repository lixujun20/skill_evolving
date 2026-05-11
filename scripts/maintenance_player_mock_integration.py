#!/usr/bin/env python3
"""Mock frontend/backend integration check for the maintenance player.

This follows the same idea as the MetaAgent mock integration harness:
exercise the real backend API contract, then simulate the frontend's
critical rendering path with deterministic data instead of relying on manual
browser inspection.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STATIC_JS = ROOT / "academic/webapp/static/maintenance.js"


def _json_get(client: Any, path: str) -> dict[str, Any]:
    response = client.get(path)
    if response.status_code != 200:
        raise AssertionError(f"GET {path} returned {response.status_code}: {response.get_data(as_text=True)[:500]}")
    payload = response.get_json()
    if not isinstance(payload, dict):
        raise AssertionError(f"GET {path} returned non-object JSON")
    return payload


def _pick_experiment(client: Any) -> dict[str, Any]:
    payload = _json_get(client, "/api/maintenance/experiments")
    experiments = payload.get("experiments") or []
    if not experiments:
        raise AssertionError("No maintenance experiments are visible to the frontend API")
    return experiments[0]


def _assert_player_contract(player: dict[str, Any]) -> None:
    frames = player.get("frames") or []
    if len(frames) < 3:
        raise AssertionError(f"Expected multiple player frames, got {len(frames)}")

    groups = {frame.get("role_group") for frame in frames}
    expected = {"executor", "retriever"}
    missing = expected - groups
    if missing:
        raise AssertionError(f"Player trace is missing expected role groups: {sorted(missing)}")

    flow_frames = [
        frame
        for frame in frames
        if frame.get("consumed_slots") or frame.get("produced_slots")
    ]
    if not flow_frames:
        raise AssertionError("No frame records consumed_slots/produced_slots")

    first_flow = flow_frames[0]
    elements = first_flow.get("elements") or {}
    for required in ("role:executor", "skill_store"):
        if required not in elements:
            raise AssertionError(f"Flow frame is missing element {required}")

    roles_seen: set[str] = set()
    elements: dict[str, Any] = dict(player.get("initial_elements") or {})
    for frame in frames:
        if player.get("snapshot_mode") == "delta":
            for element_id, element in (frame.get("element_deltas") or {}).items():
                if element is None:
                    elements.pop(element_id, None)
                else:
                    elements[element_id] = element
            frame_elements = elements
        else:
            frame_elements = frame.get("elements") or {}
        for element_id, element in frame_elements.items():
            if not str(element_id).startswith("role:"):
                continue
            state = element.get("state") or {}
            if isinstance(state.get("role_state"), dict):
                roles_seen.add(str(element_id).replace("role:", ""))
    missing_state = {"executor", "retriever"} - roles_seen
    if missing_state:
        raise AssertionError(f"Player role_state is missing for roles: {sorted(missing_state)}")


def _run_node_render_check(player: dict[str, Any]) -> None:
    frames = player.get("frames") or []
    reconstructed_frames = _reconstruct_player_frames(player)
    frame = next(
        (
            item
            for item in reconstructed_frames
            if item.get("role_group") in {"retriever", "executor", "unit_tester", "refiner"}
            and (item.get("consumed_slots") or item.get("produced_slots"))
            and any(
                str(element_id).startswith("role:")
                and isinstance((element.get("state") or {}).get("role_state"), dict)
                for element_id, element in (item.get("elements") or {}).items()
            )
        ),
        reconstructed_frames[0],
    )

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as handle:
        json.dump(frame, handle, ensure_ascii=False)
        frame_path = Path(handle.name)

    node_code = f"""
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync({json.dumps(str(STATIC_JS))}, 'utf8');
const frame = JSON.parse(fs.readFileSync({json.dumps(str(frame_path))}, 'utf8'));
const context = {{
  console,
  document: {{
    addEventListener() {{}},
    body: {{ classList: {{ add() {{}}, remove() {{}}, toggle() {{}} }} }},
    documentElement: {{ style: {{ setProperty() {{}}, removeProperty() {{}} }} }},
    querySelector() {{ return null; }},
    getElementById() {{ return null; }},
  }},
  window: {{
    addEventListener() {{}},
    location: {{ pathname: '/maintenance', search: '', hash: '' }},
    history: {{ pushState() {{}}, replaceState() {{}}, back() {{}} }},
  }},
  localStorage: {{
    getItem() {{ return null; }},
    setItem() {{}},
    removeItem() {{}},
  }},
  fetch: async () => ({{ json: async () => ({{ experiments: [] }}) }}),
  setTimeout,
  clearTimeout,
}};
vm.createContext(context);
vm.runInContext(source, context);
const scene = context.buildPlayerScene(frame);
const html = context.renderPixelPlayerBoard(scene, frame);
const selected = scene.elementsById['role:executor'] || scene.elementsById[scene.defaultSelectedId];
const inspector = context.renderPlayerInspector(selected, frame);
context.__mockFrame = frame;
const elementStore = {{}};
function makeElement(id, initial = '') {{
  return {{
    id,
    innerHTML: initial,
    textContent: '',
    value: '',
    scrollTop: 0,
    dataset: {{}},
    classList: {{ toggle() {{}}, add() {{}}, remove() {{}} }},
    querySelectorAll() {{ return []; }},
    querySelector() {{ return null; }},
  }};
}}
context.document.getElementById = (id) => {{
  if (!elementStore[id]) elementStore[id] = makeElement(id);
  return elementStore[id];
}};
context.document.querySelectorAll = (selector) => {{
  if (selector === '[data-player-slider]') return [{{ value: '', classList: {{ toggle() {{}} }} }}];
  if (selector === '.player-marker[data-frame-index]') return [{{ dataset: {{ frameIndex: '0' }}, classList: {{ toggle() {{}} }} }}];
  if (selector === '.player-overlay-head .maintenance-stage-subtitle') return [{{ textContent: '' }}];
  if (selector === '.player-toolbar .maintenance-stage-title') return [{{ textContent: '' }}];
  if (selector === '.player-toolbar .maintenance-stage-subtitle') return [{{ textContent: '' }}];
  if (selector === '.player-frame-count') return [{{ textContent: '' }}];
  return [];
}};
vm.runInContext(`
  maintenanceState.currentPlayer = {{ frames: [__mockFrame, __mockFrame], snapshot_mode: 'full' }};
  maintenanceState.currentFrameIndex = 0;
  maintenanceState.overlayStack = [{{ type: 'skill_store', payload: {{}} }}];
  this.__overlayHtml = renderPlayerOverlayStack();
  previewPlayerFrame(1);
  this.__previewBoard = document.getElementById('player-board-dynamic').innerHTML;
  this.__previewOverlay = document.getElementById('player-overlay-body-dynamic').innerHTML;
`, context);
const overlay = context.__overlayHtml || '';
const previewBoard = context.__previewBoard || '';
const previewOverlay = context.__previewOverlay || '';
function assert(condition, message) {{
  if (!condition) throw new Error(message);
}}
assert(html.includes('factory-canvas'), 'board must use fixed factory canvas');
assert(html.includes('factory-wire-layer'), 'board must render SVG wire layer');
assert(html.includes('slot-jack jack-top'), 'slot jacks must be visible in node HTML');
assert(html.includes('slot-jack jack-left'), 'left input jack must be visible in node HTML');
assert(html.includes('slot-jack jack-right'), 'right output jack must be visible in node HTML');
assert(html.includes('slot-port in-port'), 'input port badge missing');
assert(html.includes('slot-port out-port'), 'output port badge missing');
assert(inspector.includes('Role State Board'), 'inspector must expose role state board');
assert(inspector.includes('Visible Messages') || inspector.includes('Role Summary'), 'inspector must render stable role state content');
assert(inspector.includes('Last Event / Delta'), 'inspector must label raw event as delta/event, not state');
assert(!inspector.includes('Current Element State'), 'ambiguous Current Element State label must not return');
assert(overlay.includes('player-overlay-timeline'), 'overlay must keep timeline controls available');
assert(overlay.includes('Prev Mark') && overlay.includes('Next Role'), 'overlay timeline must expose marker navigation');
assert(previewBoard.includes('factory-canvas'), 'preview frame must refresh board without full page render');
assert(previewOverlay.length > 0, 'preview frame must keep overlay body refreshable');
assert(!html.includes('diagonal-edge'), 'legacy diagonal edge class must not render');
assert(!html.includes('factory-edge'), 'legacy div edge class must not render');
const pathMatches = [...html.matchAll(/<path class="factory-wire[^"]*" d="([^"]+)"/g)].map((m) => m[1]);
assert(pathMatches.length > 0, 'expected at least one circuit path');
assert(pathMatches.every((path) => !path.includes(' C ') && !path.includes(' Q ')), 'wires must be orthogonal SVG paths');
assert(pathMatches.some((path) => / V .* H /.test(path)), 'expected vertical/horizontal circuit segments');
console.log(JSON.stringify({{
  action_kind: frame.action_kind,
  role_group: frame.role_group,
  paths: pathMatches.length,
  inspector_state_board: inspector.includes('Role State Board'),
  consumed: frame.consumed_slots || [],
  produced: frame.produced_slots || [],
}}));
"""

    try:
        result = subprocess.run(
            ["node", "-e", node_code],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
    finally:
        frame_path.unlink(missing_ok=True)

    if result.returncode != 0:
        raise AssertionError(
            "Node frontend render check failed\n"
            f"STDOUT:\n{result.stdout}\n"
            f"STDERR:\n{result.stderr}"
        )
    print(f"[render] {result.stdout.strip()}")


def _reconstruct_player_frames(player: dict[str, Any]) -> list[dict[str, Any]]:
    frames = player.get("frames") or []
    if player.get("snapshot_mode") != "delta":
        return [dict(frame) for frame in frames]
    elements: dict[str, Any] = dict(player.get("initial_elements") or {})
    reconstructed: list[dict[str, Any]] = []
    for frame in frames:
        for element_id, element in (frame.get("element_deltas") or {}).items():
            if element is None:
                elements.pop(element_id, None)
            else:
                elements[element_id] = element
        next_frame = dict(frame)
        next_frame["elements"] = json.loads(json.dumps(elements))
        reconstructed.append(next_frame)
    return reconstructed


def main() -> int:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    from academic.webapp.app import app

    client = app.test_client()
    experiment = _pick_experiment(client)
    experiment_id = experiment["id"]
    print(f"[api] experiment={experiment_id}")

    detail = _json_get(client, f"/api/maintenance/experiment?id={experiment_id}")
    if not detail.get("pages"):
        raise AssertionError("Experiment detail has no pages")
    print(f"[api] pages={len(detail.get('pages') or [])}")

    player = _json_get(client, f"/api/maintenance/player?id={experiment_id}")
    _assert_player_contract(player)
    print(f"[api] frames={len(player.get('frames') or [])}")

    _run_node_render_check(player)
    print("MAINTENANCE_PLAYER_MOCK_INTEGRATION_PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
