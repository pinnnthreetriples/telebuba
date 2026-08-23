// The design's pill switch (track + sliding thumb), 18px of travel.
//
// Lives in shared/ui rather than beside the settings form it started in: steiger's
// `fsd/forbidden-imports` bars page→page imports, so every other screen that wants
// this control would otherwise hand-roll its own copy — and the app already carries
// two such copies (widgets/warming-board/ui/WarmConfigModal and
// pages/neurocomment/ui/CaptchaSolverCard).
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
      className={`tb-sw relative h-[26px] w-[44px] shrink-0 rounded-full transition-colors disabled:opacity-50 ${checked ? 'bg-primary' : 'bg-line-strong'}`}
    >
      <span
        className={`tb-sw-thumb absolute top-[3px] block h-5 w-5 rounded-full bg-white shadow-[0_1px_3px_rgba(0,0,0,0.3)] transition-transform ${checked ? 'translate-x-[21px]' : 'translate-x-[3px]'}`}
      />
    </button>
  );
}
