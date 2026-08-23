import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';

import { getToasts, subscribe, type Toast } from './toast';

// Renders the toast queue (see toast.ts). Mounted once at the app root; styling
// matches the design's dark tooltip (#16161A).
//
// Portalled into document.body, and NOT rendered in place: toasts share the
// `dialog` z rung with Modal (there is no fifth layer above it), so the tie is
// broken by document order. Modal portals to body too, and a toast is almost
// always raised by an action taken inside an open dialog — mounting the stack
// into body at that moment puts it after the dialog, i.e. on top. Left in the
// app root it would sit inside #root, which precedes every modal portal, and
// every toast a modal fired would be lost behind that modal's backdrop.
export function Toaster() {
  const [items, setItems] = useState<Toast[]>(getToasts);
  useEffect(() => subscribe(setItems), []);

  if (items.length === 0) return null;
  return createPortal(
    <div className="pointer-events-none fixed bottom-5 left-1/2 z-dialog flex -translate-x-1/2 flex-col items-center gap-[10px]">
      {items.map((toast) => (
        <div
          key={toast.id}
          role="alert"
          className="pointer-events-auto max-w-[90vw] rounded-lg bg-term px-[14px] py-[10px] text-[13px] leading-[1.5] text-white shadow-[0_6px_20px_rgba(0,0,0,0.18)] [animation:fadeup_0.25s_ease]"
        >
          {toast.message}
        </div>
      ))}
    </div>,
    document.body,
  );
}
