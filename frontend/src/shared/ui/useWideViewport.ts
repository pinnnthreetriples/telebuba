import { type RefObject, useLayoutEffect, useState, useSyncExternalStore } from 'react';

import { breakpoint } from '@/shared/design-system';

// Both numbers come from `breakpoint` in the token tree, which is also where Tailwind
// reads its `screens`. They were written here as well until this change — 880 twice and
// 1024 three times, counting Tailwind's own `lg` default that the app wears 27 times —
// and a breakpoint that CSS and JavaScript each keep their own copy of is a layout that
// can disagree with itself about which layout it is in.
//
// The viewport query is only the fallback: `useWideContainer` below is the exported
// decision, because a viewport query cannot see that the table's own box is narrower than
// the viewport (a column, a modal).
const WIDE_MQ = `(min-width: ${String(breakpoint.wide)}px)`;

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
// Read synchronously on first render, so there is no table-then-cards flash. Private:
// the exported decision is useWideContainer below, and callers outside DataTable have to
// use the same one it does — a select-all relocated on a different query than the layout
// it compensates for goes missing (or doubles).
function useWideViewport(): boolean {
  return useSyncExternalStore(subscribe, getSnapshot);
}

// The same table-or-cards decision, keyed to the width the table actually gets
// instead of the viewport's. The viewport query was wrong wherever a table does not
// span the page: on the neurocomment screen a 1024px tablet gives the board column
// 620px and the config rail 340px, so both tables rendered "wide" and their card
// scrolled sideways over the 880px floor — and the captcha queue did so on a desktop
// too, in the 340px rail. Falls back to the viewport while the element is
// unmeasurable: first render, a display:none ancestor, or a test DOM that reports
// every box as 0×0.
export function useWideContainer(ref: RefObject<HTMLElement | null>): boolean {
  const [width, setWidth] = useState(0);
  const viewportWide = useWideViewport();
  // Layout effect, so the corrected layout is the first one painted.
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    setWidth(el.clientWidth);
    // Guarded like AppNav's: the test DOM has no ResizeObserver, and there the
    // fallback above is the whole story anyway.
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(() => {
      setWidth(el.clientWidth);
    });
    observer.observe(el);
    return () => {
      observer.disconnect();
    };
  }, [ref]);
  return width > 0 ? width >= breakpoint.table : viewportWide;
}
