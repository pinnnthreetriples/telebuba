import { useId } from 'react';

import { Icon } from '@/shared/ui';

// The slice's checkbox row (label + square box), shared by the channel create
// and edit dialogs. Extracted when the second dialog needed the same control —
// internal to the slice, like ./_channelsShared and ./_styles.
// `hint` is a caption under the label — e.g. why the row is disabled. It is the
// checkbox's description, not part of its name (hence the explicit aria-label).
export function CheckRow({
  label,
  on,
  disabled,
  hint,
  onToggle,
}: {
  label: string;
  on: boolean;
  disabled?: boolean;
  hint?: string;
  onToggle: () => void;
}) {
  const hintId = useId();
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={on}
      aria-label={label}
      aria-describedby={hint ? hintId : undefined}
      disabled={disabled}
      onClick={onToggle}
      className="mb-4 flex w-full items-center gap-3 text-left disabled:opacity-60"
    >
      <span
        className={`flex size-glyph shrink-0 items-center justify-center rounded-sm border ${on ? 'border-action-primary bg-action-primary' : 'border-line bg-surface-card'}`}
      >
        {on && <Icon name="check" size={14} className="stroke-on-fill" />}
      </span>
      <span className="flex flex-col">
        <span className="type-body text-content-secondary">{label}</span>
        {hint && (
          <span id={hintId} className="type-small">
            {hint}
          </span>
        )}
      </span>
    </button>
  );
}
