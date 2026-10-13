import { FOCUS_RING } from '@/shared/design-system';
import { cn } from '@/shared/lib/cn';

// A native range input in the action colour. Native on purpose: arrows, Home/End and
// PageUp/PageDown, and the value a screen reader announces, all come with the element.
// `valueText` is that announcement when the bare number means nothing ("1–3 min").
export function Slider({
  value,
  min,
  max,
  step = 1,
  onChange,
  label,
  valueText,
  disabled = false,
  className,
}: {
  value: number;
  min: number;
  max: number;
  step?: number;
  onChange: (value: number) => void;
  label: string;
  valueText?: string;
  disabled?: boolean;
  className?: string;
}) {
  return (
    <input
      type="range"
      min={min}
      max={max}
      step={step}
      value={value}
      disabled={disabled}
      aria-label={label}
      aria-valuetext={valueText}
      onChange={(event) => {
        onChange(Number(event.target.value));
      }}
      className={cn(
        'w-full cursor-pointer rounded-full accent-action-primary disabled:cursor-default disabled:opacity-50',
        FOCUS_RING,
        className,
      )}
    />
  );
}
