// The one stroke rule every glyph in the app follows, said once so the glyphs `Icon`
// does not draw — the animated ones DeleteButton draws stroke by stroke — weigh the same
// as the ones it does. The reasoning lives beside `Icon`, which owned it first.
//
// A stroke width is in viewBox units (24), so what the eye sees is `width * size / 24`.
// The rule holds that at 1.3 CSS px for every size, rounded to a tenth.
const STROKE_PX = 1.3;

export function iconStroke(size: number): number {
  return Math.round((STROKE_PX * 24 * 10) / size) / 10;
}
