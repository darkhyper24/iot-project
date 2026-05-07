#!/usr/bin/env python3
"""Phase 3 B.4 — generate one SVG floor plan per floor.

Output: thingsboard/assets/floor_plans/floor-##.svg

Uses the same 5×4 grid as ``scripts/seed_thingsboard.py``'s room metadata, so polygon
coordinates seeded as Room asset server-attrs (coordinates_x / coordinates_y)
align with the visual rooms in the SVG.

These can be uploaded as the background image for the Image Map widget on the
Phase 3 ``Campus Floor Map`` dashboard. ThingsBoard's image widgets accept SVG.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "thingsboard/assets/floor_plans"

GRID_COLS = 5
GRID_ROWS = 4
W, H = 1000, 1000
CELL_W = W // GRID_COLS  # 200
CELL_H = H // GRID_ROWS  # 250


def _floor_svg(floor: int) -> str:
    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
        'width="1000" height="1000" font-family="sans-serif">',
        '<rect width="100%" height="100%" fill="#fafafa"/>',
        f'<text x="20" y="40" font-size="28" fill="#333">Floor {floor:02d}</text>',
    ]
    for idx in range(GRID_COLS * GRID_ROWS):
        col = idx % GRID_COLS
        row = idx // GRID_COLS
        x = col * CELL_W
        y = row * CELL_H
        room_on_floor = idx + 1
        is_mqtt = room_on_floor <= 10
        fill = "#e8f0fe" if is_mqtt else "#fef0e8"
        parts.append(
            f'<rect x="{x + 10}" y="{y + 10}" width="{CELL_W - 20}" height="{CELL_H - 20}" '
            f'fill="{fill}" stroke="#888" stroke-width="2"/>'
        )
        label = f"r{room_on_floor:03d}"
        kind = "MQTT" if is_mqtt else "CoAP"
        parts.append(
            f'<text x="{x + CELL_W // 2}" y="{y + CELL_H // 2 - 6}" '
            f'text-anchor="middle" font-size="22" fill="#222">{label}</text>'
        )
        parts.append(
            f'<text x="{x + CELL_W // 2}" y="{y + CELL_H // 2 + 22}" '
            f'text-anchor="middle" font-size="14" fill="#666">{kind}</text>'
        )
        # Layer 20 rooms over the same grid: rows 5..8 wrap into the same 5x4
        # by overlaying half-opacity tiles for rooms 11-20.
        if room_on_floor > 10:
            pass  # CoAP already styled differently
    parts.append("</svg>")
    return "\n".join(parts)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for floor in range(1, 11):
        path = OUT_DIR / f"floor-{floor:02d}.svg"
        path.write_text(_floor_svg(floor), encoding="utf-8")
        print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
