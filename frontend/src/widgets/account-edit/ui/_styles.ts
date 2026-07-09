// Shared field/label/segmented-control styles + the check-state type used across
// the AccountEdit sections. Non-component module (keeps _shared.tsx components-only
// for React Fast Refresh).

export const FIELD =
  'tb-time w-full rounded-[10px] border border-line-input bg-white px-3 py-[9px] text-[13px] outline-none';
export const FIELD_LOCKED =
  'w-full cursor-not-allowed rounded-[10px] border border-line bg-[#f6f5f2] px-3 py-[9px] text-[13px] text-ink-subtle outline-none';
export const LABEL = 'mb-[6px] block text-[12px] font-medium text-[#3a3a3a]';
export const SEG_WRAP = 'mb-[10px] flex gap-1 rounded-[10px] bg-[#f1efed] p-1';
export const seg = (on: boolean): string =>
  `flex-1 rounded-[7px] py-[7px] text-[12.5px] font-medium transition ${on ? 'bg-white text-ink shadow-sm' : 'text-ink-muted'}`;

// A check-button drives a tiny idle→loading→(ok|err) machine, settling back to
// idle. Backed by real check calls (proxy connectivity / @SpamBot / alive).
export type CheckState = 'idle' | 'loading' | 'ok' | 'err';
