// Shared label style + the check-state type used across the AccountEdit sections.
// Non-component module (keeps _shared.tsx components-only for React Fast Refresh).
//
// The segmented-tray pair that used to live here (SEG_WRAP + seg) is now
// `shared/ui/SegmentedControl`, which the five sections that wore it share with nine
// more sites outside this slice.

export const LABEL = 'mb-tight block type-label';

// A check-button drives a tiny idle→loading→(ok|err) machine, settling back to
// idle. Backed by real check calls (proxy connectivity / @SpamBot / alive).
export type CheckState = 'idle' | 'loading' | 'ok' | 'err';
