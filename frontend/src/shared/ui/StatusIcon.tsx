import { Icon } from './Icon';

// Shared checkmark/x glyph for mutation success/error feedback (rule: every
// mutation shows a green check or red cross). Callers wrap this in whichever
// entrance class (.tb-pop / .tb-blur / .tb-swapin) fits their context.
export function StatusIcon({ kind }: { kind: 'ok' | 'err' }) {
  return kind === 'ok' ? <Icon name="check" size={14} /> : <Icon name="close" size={14} />;
}
