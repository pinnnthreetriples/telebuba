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
        className={`flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-sm border ${on ? 'border-primary bg-primary' : 'border-line-input bg-white'}`}
      >
        {on && (
          <svg
            width="13"
            height="13"
            viewBox="0 0 24 24"
            fill="none"
            className="stroke-white"
            strokeWidth="3"
          >
            <path d="M20 6 9 17l-5-5" />
          </svg>
        )}
      </span>
      <span className="text-lead text-ink-body">{label}</span>
    </button>
  );
}
