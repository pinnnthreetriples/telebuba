// Shared checkmark/x glyph for mutation success/error feedback (rule: every
// mutation shows a green check or red cross). Callers wrap this in whichever
// entrance class (.tb-pop / .tb-blur / .tb-swapin) fits their context.
export function StatusIcon({ kind }: { kind: 'ok' | 'err' }) {
  return kind === 'ok' ? (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="3"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M20 6 9 17l-5-5" />
    </svg>
  ) : (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.6"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M18 6 6 18M6 6l12 12" />
    </svg>
  );
}
