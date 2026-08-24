import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';

import { getToasts, subscribe, type Toast } from './toast';

// Renders the toast queue (see toast.ts). Mounted once at the app root; styling
// matches the design's dark tooltip (#16161A).
//
// Portalled into document.body rather than rendered in place: the stack is `fixed`,
// and a `fixed` element inside an ancestor that has a transform or a filter is
// positioned against that ancestor instead of the viewport. Nothing on the page
// does that today, but a page-level animation is one class away from it, and a
// toast that lands in the middle of a card rather than at the bottom of the screen
// is a hard thing to trace back to a class on an ancestor.
//
// The stack sits on its own `z-toast` rung, one above `z-dialog`: a toast reports
// the outcome of an action, and the dialog that action was taken in is usually
// still open behind it.
export function Toaster() {
  const [items, setItems] = useState<Toast[]>(getToasts);
  useEffect(() => subscribe(setItems), []);

  if (items.length === 0) return null;
  return createPortal(
    <div className="pointer-events-none fixed bottom-2xl left-1/2 z-toast flex -translate-x-1/2 flex-col items-center gap-md">
      {items.map((toast) => (
        <div
          key={toast.id}
          role="alert"
          className="pointer-events-auto max-w-[90vw] rounded-lg bg-term px-lg py-md text-lead leading-[1.5] text-white shadow-pop [animation:fadeup_0.25s_ease]"
        >
          {toast.message}
        </div>
      ))}
    </div>,
    document.body,
  );
}
