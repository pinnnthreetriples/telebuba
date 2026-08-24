// Shared label/segmented-control styles + the check-state type used across
// the AccountEdit sections. Non-component module (keeps _shared.tsx components-only
// for React Fast Refresh).

export const LABEL = 'mb-tight block text-body font-medium text-ink-body';
export const SEG_WRAP = 'mb-md flex gap-tight rounded-lg bg-canvas p-xs';
// The canon's segmented rung is `8px 10px`; only the vertical half is applied. These
// segments are `flex-1` in a fixed tray, so their width comes from the tray and a 10px
// horizontal padding would change nothing except the point at which a long label forces
// the tray to overflow.
export const seg = (on: boolean): string =>
  `flex-1 rounded-sm py-sm text-body font-medium transition ${on ? 'bg-white text-ink shadow-seg' : 'text-ink-muted'}`;

// A check-button drives a tiny idle→loading→(ok|err) machine, settling back to
// idle. Backed by real check calls (proxy connectivity / @SpamBot / alive).
export type CheckState = 'idle' | 'loading' | 'ok' | 'err';
