import { useRef } from 'react';

// Arrow/Home/End steps, given the current value and the track's ends.
const SLIDER_KEYS: Record<string, (value: number, min: number, max: number) => number> = {
  ArrowLeft: (v, min) => Math.max(min, v - 1),
  ArrowRight: (v, _min, max) => Math.min(max, v + 1),
  Home: (_v, min) => min,
  End: (_v, _min, max) => max,
};

// The start dialog's draggable whole-number track: the warming length and the first
// cycle's delay wear the same one. Dots mark the presets, not every step: thirty dots
// on one track read as noise.
export function RangeSlider({
  value,
  min,
  max,
  ticks,
  label,
  valueText,
  onChange,
}: {
  value: number;
  min: number;
  max: number;
  ticks: readonly number[];
  label: string;
  valueText: (n: number) => string;
  onChange: (n: number) => void;
}) {
  const trackRef = useRef<HTMLDivElement>(null);
  const ratio = (n: number) => ((n - min) / (max - min)) * 100;
  const pct = ratio(value);

  const setFromClientX = (clientX: number) => {
    const el = trackRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const r = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
    onChange(Math.round(min + r * (max - min)));
  };

  return (
    <>
      <div
        ref={trackRef}
        role="slider"
        tabIndex={0}
        aria-valuemin={min}
        aria-valuemax={max}
        aria-valuenow={value}
        aria-valuetext={valueText(value)}
        aria-label={label}
        onPointerDown={(e) => {
          e.currentTarget.setPointerCapture(e.pointerId);
          setFromClientX(e.clientX);
        }}
        onPointerMove={(e) => {
          if (e.buttons === 1) setFromClientX(e.clientX);
        }}
        onKeyDown={(e) => {
          const step = SLIDER_KEYS[e.key];
          if (!step) return;
          // Home/End would otherwise also scroll the dialog under the slider.
          e.preventDefault();
          onChange(step(value, min, max));
        }}
        className="relative mx-3 mb-2 h-compact cursor-grab touch-none select-none outline-hidden"
      >
        <div className="absolute inset-x-0 top-1/2 h-meter -translate-y-1/2 overflow-hidden rounded-full bg-canvas">
          <div
            className="h-full rounded-full bg-action-primary"
            style={{ width: `${String(pct)}%` }}
          />
        </div>
        <div className="pointer-events-none absolute inset-x-0 top-1/2 h-meter -translate-y-1/2">
          {ticks.map((n) => (
            <span
              key={n}
              className="absolute top-1/2 size-tick -translate-x-1/2 -translate-y-1/2 rounded-full bg-line-strong"
              style={{ left: `${String(ratio(n))}%` }}
            />
          ))}
        </div>
        <div
          className="absolute top-1/2 size-glyph -translate-x-1/2 -translate-y-1/2 rounded-full border-[2px] border-action-primary bg-surface-card shadow-thumb"
          style={{ left: `${String(pct)}%` }}
        />
      </div>
      <div className="mx-3 flex justify-between type-small">
        <span>{valueText(min)}</span>
        <span>{valueText(max)}</span>
      </div>
    </>
  );
}
