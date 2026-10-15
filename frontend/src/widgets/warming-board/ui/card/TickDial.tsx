import type { ReactNode } from 'react';

// A dial of ticks: the lit share is the value, the colour its meaning. The ticks light
// up one after another on mount, so the dial fills instead of popping in. Both card
// dials (worked days, trust) are this one drawing, so they read as a pair.
const TICKS = 36;
const SWEEP = 270;
const START = 135;
const CENTER = 60;
const OUTER = 56;
const INNER = 47;
const TICK_STAGGER_MS = 18;

function tick(index: number): { x1: number; y1: number; x2: number; y2: number } {
  const angle = ((START + (SWEEP * index) / (TICKS - 1)) * Math.PI) / 180;
  const cos = Math.cos(angle);
  const sin = Math.sin(angle);
  return {
    x1: CENTER + INNER * cos,
    y1: CENTER + INNER * sin,
    x2: CENTER + OUTER * cos,
    y2: CENTER + OUTER * sin,
  };
}

export function TickDial({
  share,
  tone,
  label,
  size = 112,
  children,
}: {
  // 0..1 — how much of the dial is lit.
  share: number;
  // A text-colour class; the lit ticks paint in it.
  tone: string;
  // What the dial says, for a screen reader.
  label: string;
  // Drawn size in px; the drawing scales, the ticks keep their proportions.
  size?: number;
  // The centre: the number and its caption.
  children: ReactNode;
}) {
  const lit = Math.round(Math.max(0, Math.min(1, share)) * TICKS);
  return (
    <div className="relative">
      <svg width={size} height={size} viewBox="0 0 120 120" role="img" aria-label={label}>
        {Array.from({ length: TICKS }, (_, index) => {
          const on = index < lit;
          return (
            <line
              key={index}
              {...tick(index)}
              stroke="currentColor"
              strokeWidth="3"
              strokeLinecap="round"
              className={on ? `tb-tick ${tone}` : 'text-line'}
              style={on ? { animationDelay: `${String(index * TICK_STAGGER_MS)}ms` } : undefined}
            />
          );
        })}
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">{children}</div>
    </div>
  );
}
