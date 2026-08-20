// Shared field/label/segmented-control styles + the check-state type used across
// the AccountEdit sections. Non-component module (keeps _shared.tsx components-only
// for React Fast Refresh).

export const FIELD =
  'tb-time w-full rounded-[10px] border border-line-input bg-white px-3 py-[9px] text-[13px] outline-none';
// A greyed-out field the operator cannot type into but CAN select and copy from —
// the 2FA reveal panel's password, where selecting it by hand is the documented
// fallback whenever the clipboard write fails or there is no clipboard at all.
// `cursor-not-allowed` there would say the opposite of the instruction beside it.
export const FIELD_READONLY =
  'w-full rounded-[10px] border border-line bg-[#f6f5f2] px-3 py-[9px] text-[13px] text-ink-subtle outline-none';
// The same look for a genuinely `disabled` input (the device facts), where the cursor
// is telling the truth.
export const FIELD_LOCKED = `cursor-not-allowed ${FIELD_READONLY}`;
export const LABEL = 'mb-[6px] block text-[12px] font-medium text-[#3a3a3a]';
export const SEG_WRAP = 'mb-[10px] flex gap-1 rounded-[10px] bg-[#f1efed] p-1';
export const seg = (on: boolean): string =>
  `flex-1 rounded-[7px] py-[7px] text-[12.5px] font-medium transition ${on ? 'bg-white text-ink shadow-sm' : 'text-ink-muted'}`;

// A check-button drives a tiny idle→loading→(ok|err) machine, settling back to
// idle. Backed by real check calls (proxy connectivity / @SpamBot / alive).
export type CheckState = 'idle' | 'loading' | 'ok' | 'err';
