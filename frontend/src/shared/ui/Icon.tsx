import {
  ArrowLeftRight,
  ArrowRight,
  AudioLines,
  Check,
  ChevronDown,
  ChevronRight,
  CircleCheck,
  CircleX,
  CloudUpload,
  Eye,
  EyeOff,
  FileIcon,
  Globe,
  Paperclip,
  Pause,
  PencilLine,
  Play,
  Plus,
  RotateCw,
  Settings,
  ShieldCheck,
  Sparkles,
  Trash,
  TriangleAlert,
  Video,
  X,
  type LucideIcon,
} from 'lucide-react';

import { AlertSquare } from './iconsLocal';

// The app's glyphs, drawn once each. Before this they were 98 hand-copied <svg>
// elements of 23 shapes across 24 files, and every copy re-decided its own weight:
// ten different `strokeWidth` values app-wide, six of them on the checkmark alone,
// and `strokeLinecap` set on 28 elements out of 131. The same check read visibly
// heavier in a warming card than in the dialog beside it.
//
// Those 23 shapes were a transcription of Lucide, and a transcription drifts. Ours
// had: Feather's cog where Lucide has redrawn it, an eye with pointed corners where
// Lucide rounds them, a camera body 16 units tall against the library's 12, a pencil
// missing its ferrule. The library is a dependency now and the transcription is gone.
// `iconsLocal.ts` keeps the one shape Lucide has no glyph for.
//
// Weight is still derived here and there is still no prop for it. That is the whole
// point: a caller that can pass a stroke is a caller that will pick a different one.

// The app's name for a glyph on the left, Lucide's on the right. The two disagree
// often enough — `chart` is `audio-lines`, `refresh` is `rotate-cw`, `close` is `x` —
// that renaming the call sites to match the library would be a larger and more
// breakable diff than this table, and it would spend the app's vocabulary on the
// library's. Not exported, so `react-refresh/only-export-components` stays quiet and
// the table can live beside the component that reads it.
const GLYPH = {
  'alert-square': AlertSquare,
  'alert-triangle': TriangleAlert,
  'arrow-right': ArrowRight,
  'arrow-swap': ArrowLeftRight,
  chart: AudioLines,
  check: Check,
  'check-circle': CircleCheck,
  'chevron-down': ChevronDown,
  'chevron-right': ChevronRight,
  close: X,
  eye: Eye,
  'eye-off': EyeOff,
  file: FileIcon,
  gear: Settings,
  globe: Globe,
  paperclip: Paperclip,
  pause: Pause,
  // `pencil`, not `pencil-line`, would drop the baseline stroke the app's edit glyph
  // has always carried.
  pencil: PencilLine,
  play: Play,
  plus: Plus,
  refresh: RotateCw,
  'shield-check': ShieldCheck,
  sparkles: Sparkles,
  trash: Trash,
  'upload-cloud': CloudUpload,
  video: Video,
  'x-circle': CircleX,
} satisfies Record<string, LucideIcon>;

export type IconName = keyof typeof GLYPH;

// The even rungs from 10 to 20. The branch this replaced used fourteen pixel sizes
// between 9 and 22, and every one of them is within 1px of a rung except the single
// 22px upload mark, which is 2px from 20 — so the odd sizes round up and nobody can
// see it, while leaving 13/15/17 in the type would preserve the disagreement.
export type IconSize = 10 | 12 | 14 | 16 | 18 | 20;

// A stroke width is in viewBox units, so what the eye actually sees is
// `strokeWidth * size / 24`. Across the 119 stroked icons this replaced that lands at
// a median of 1.28 CSS px and a mean of 1.30 — flat enough to be a constant that was
// never written down. This writes it down: 1.3px at every rung, which works out to
// 2.0 at 16px and 2.2 at 14px, the two spellings the app already used most.
const STROKE_PX = 1.3;

export function Icon({
  name,
  size,
  className,
}: {
  name: IconName;
  // No default. A rung left unstated is a rung nobody chose, and that is how the
  // fourteen sizes happened in the first place.
  size: IconSize;
  className?: string;
}) {
  const Glyph = GLYPH[name];
  // Lucide draws everything as an outline, transport controls included, and a hollow
  // play button is a different button. Both shapes are closed — Lucide's play is one
  // path with a `z`, its pause two rects — so painting them instead of stroking them
  // is a pair of props rather than a second copy of the geometry.
  const solid = name === 'play' || name === 'pause';
  return (
    <Glyph
      size={size}
      // `absoluteStrokeWidth` is Lucide's name for this same rule and computes the
      // same quantity, but unrounded: 16px would render 1.95 where every one of the
      // sites this replaced wrote 2. Rounding to a tenth keeps the six numbers the
      // codebase already used, so the width goes over as a number, not as the flag.
      strokeWidth={Math.round((STROKE_PX * 24 * 10) / size) / 10}
      className={className}
      {...(solid ? { fill: 'currentColor', stroke: 'none' } : {})}
    />
  );
}
