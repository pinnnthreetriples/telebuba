# Telebuba icon set — drawing spec

One set, drawn from scratch, every glyph to the same rules. The UI is calm and rounded
(Inter, white cards, 12px radii, soft tints), so the icons are friendly outline glyphs:
round ends, round joins, generous counters, no sharp spikes, no hairline details.

## Canvas
- `viewBox="0 0 24 24"`, `width="24" height="24"`, `xmlns="http://www.w3.org/2000/svg"`.
- Root attributes exactly: `fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"`.
  The app overrides `stroke-width` per size, so never set stroke-width on children.
- Live area 2–22 (2px padding). Nothing may touch the canvas edge.
- Coordinates on the integer or .5 grid only. No `transform`, no `style`, no `class`, no ids, no comments, no `<g>` unless needed.

## Keylines (pick the one that fits, so glyphs read the same size)
- Circle: `cx=12 cy=12 r=9` (diameter 18).
- Square: x=3 y=3 w=18 h=18, rx=3.
- Portrait rect: x=5 y=2.5 w=14 h=19, rx=2.5.
- Landscape rect: x=2.5 y=5 w=19 h=14, rx=2.5.
- Small badge (plus/check/x added to a glyph): centred on (18,18), arms 3 long, the base glyph trimmed around it with a ~2px gap — never overlap strokes.

## Shape language
- Corners of containers: rx 2–3 (large), 1–1.5 (small parts). Never 0.
- Minimum gap between parallel strokes: 2.5 units. Minimum stroke segment: 2 units.
- Arrowheads: open chevrons, arms 4–5 units at 90°, not filled triangles.
- Chevrons: arms span 6 units (e.g. `m6 9 6 6 6-6`).
- Circles of dots (grip, more): `r=1` circles drawn as stroked zero-length paths or `<circle r="1">` with `fill="currentColor"` — be consistent: use `<circle r="1" fill="currentColor" stroke="none"/>`.
- Filled glyphs (only play, pause, heart): shape gets `fill="currentColor"`, keep the stroke so corners stay rounded.
- Elements allowed: `path`, `circle`, `rect`, `line`, `polyline`, `ellipse`.

## Files
- One glyph per file: `design/icons/svg/<name>.svg`, kebab-case name from the brief.
- Must parse as XML. Keep it minimal: as few elements as the drawing needs.
