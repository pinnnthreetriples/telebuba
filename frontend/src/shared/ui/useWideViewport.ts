import { useSyncExternalStore } from 'react';

// The width at which DataTable shows a table rather than cards. 880px is the table's
// own minimum and the shell gives content viewport−48px (AppShell: mx-auto
// max-w-[1340px] px-4/px-6), so 1024 is the narrowest viewport where a full-width
// page table fits without horizontal scroll. A table inside a fixed-width modal still
// scrolls above 1024 (pre-existing) — a viewport query cannot see container width,
// and container queries are out of scope.
const WIDE_MQ = '(min-width: 1024px)';

function subscribe(onChange: () => void): () => void {
  const mql = window.matchMedia(WIDE_MQ);
  mql.addEventListener('change', onChange);
  return () => {
    mql.removeEventListener('change', onChange);
  };
}

function getSnapshot(): boolean {
  return window.matchMedia(WIDE_MQ).matches;
}

// A media *query* rather than `hidden lg:table` + `lg:hidden`, so exactly one tree is
// ever in the DOM. The CSS form renders every cell, handler and accessible name twice,
// and Tailwind never runs in the test pipeline — so `hidden` is inert under happy-dom
// and both copies answer every testing-library query.
//
// Callers outside DataTable need this for the same reason: to relocate a control the
// card layout drops (a select-all that only exists in a column header) without leaving
// two of them in the DOM. Read synchronously on first render, so there is no
// table-then-cards flash.
export function useWideViewport(): boolean {
  return useSyncExternalStore(subscribe, getSnapshot);
}
