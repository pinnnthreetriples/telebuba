import { useEffect, useState } from 'react';

import { getToasts, subscribe, type Toast } from './toast';

// Renders the toast queue (see toast.ts). Mounted once at the app root; styling
// matches the design's dark tooltip (#16161A).
export function Toaster() {
  const [items, setItems] = useState<Toast[]>(getToasts);
  useEffect(() => subscribe(setItems), []);

  if (items.length === 0) return null;
  return (
    <div className="pointer-events-none fixed bottom-5 left-1/2 z-[90] flex -translate-x-1/2 flex-col items-center gap-[10px]">
      {items.map((toast) => (
        <div
          key={toast.id}
          role="alert"
          className="pointer-events-auto max-w-[90vw] rounded-lg bg-[#16161a] px-[14px] py-[10px] text-[13px] leading-[1.5] text-white shadow-[0_6px_20px_rgba(0,0,0,0.18)] [animation:fadeup_0.25s_ease]"
        >
          {toast.message}
        </div>
      ))}
    </div>
  );
}
