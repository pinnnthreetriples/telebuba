// The design's pill switch (track + sliding thumb), 18px of travel.
//
// Lives in shared/ui rather than beside the settings form it started in: steiger's
// `fsd/forbidden-imports` bars page→page imports, so every other screen that wants
// this control would otherwise hand-roll its own copy — and the app already carried
// two such copies (pages/neurocomment/ui/CaptchaSolverCard was one of them).
export function Switch({
  checked,
  onChange,
  label,
  disabled = false,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  label: string;
  // For a switch the screen shows because the feature is coming, but that nothing
  // is wired to yet: a toggle that moves and changes nothing is worse than one
  // that plainly refuses.
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => {
        onChange(!checked);
      }}
      // eslint-disable-next-line design-tokens/no-raw-values -- see the note in the rule: the track is its own knob's travel (3 + size-chip + 21)
      className={`tb-sw relative h-compact w-[46px] shrink-0 rounded-full transition-colors disabled:opacity-50 ${checked ? 'bg-action-primary' : 'bg-line-strong'}`}
    >
      <span
        className={`tb-sw-thumb absolute top-[3px] block size-chip rounded-full bg-surface-card shadow-thumb transition-transform ${checked ? 'translate-x-[21px]' : 'translate-x-[3px]'}`}
      />
    </button>
  );
}
