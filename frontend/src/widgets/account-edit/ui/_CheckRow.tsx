import { Icon } from '@/shared/ui';

// The slice's checkbox row (label + square box), shared by the channel create
// and edit dialogs. Extracted when the second dialog needed the same control —
// internal to the slice, like ./_channelsShared and ./_styles.
export function CheckRow({
  label,
  on,
  disabled,
  onToggle,
}: {
  label: string;
  on: boolean;
  disabled?: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={on}
      disabled={disabled}
      onClick={onToggle}
      className="mb-lg flex w-full items-center gap-md text-left disabled:opacity-60"
    >
      <span
        className={`flex size-glyph shrink-0 items-center justify-center rounded-sm border ${on ? 'border-action-primary bg-action-primary' : 'border-line bg-surface-card'}`}
      >
        {on && <Icon name="check" size={14} className="stroke-on-action" />}
      </span>
      <span className="type-dialog-body text-content-secondary">{label}</span>
    </button>
  );
}
