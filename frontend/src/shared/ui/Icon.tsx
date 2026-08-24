import { ICONS, type IconDef, type IconName } from './icons';

// The app's glyphs, drawn once each. Before this they were 98 hand-copied <svg>
// elements of 23 shapes across 24 files, and every copy re-decided its own weight:
// ten different `strokeWidth` values app-wide, six of them on the checkmark alone,
// and `strokeLinecap` set on 28 elements out of 131. The same check read visibly
// heavier in a warming card than in the dialog beside it.
//
// Weight is derived here and there is no prop for it. That is the whole point: a
// caller that can pass a stroke is a caller that will pick a different one.

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
  const icon: IconDef = ICONS[name];
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      className={className}
      {...(icon.fill
        ? { fill: 'currentColor' }
        : {
            fill: 'none',
            stroke: 'currentColor',
            strokeWidth: Math.round((STROKE_PX * 24 * 10) / size) / 10,
            // Round, always. These outlines are authored for it — the exclamation
            // dots are zero-length subpaths that a butt cap renders as nothing, and
            // the chevrons come to a mitre spike.
            strokeLinecap: 'round' as const,
            strokeLinejoin: 'round' as const,
          })}
    >
      {icon.parts.map((part, index) =>
        'd' in part ? (
          <path key={index} d={part.d} />
        ) : 'r' in part ? (
          <circle key={index} {...part} />
        ) : (
          <rect key={index} {...part} />
        ),
      )}
    </svg>
  );
}
