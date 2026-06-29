import { type ReactNode, useEffect } from 'react';
import { createPortal } from 'react-dom';

// The design's modal shell: a fixed dimmed backdrop (ovfade) centering a white
// card (fadeup). Backdrop-click and Escape close; the card stops propagation.
// z and backdrop opacity match the design's per-modal values.
export function Modal({
  onClose,
  children,
  className = 'w-[420px]',
  z = 70,
  backdrop = 0.4,
}: {
  onClose: () => void;
  children: ReactNode;
  className?: string;
  z?: number;
  backdrop?: number;
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('keydown', onKey);
    };
  }, [onClose]);

  return createPortal(
    <div
      role="presentation"
      onClick={onClose}
      className="fixed inset-0 flex items-center justify-center p-5 [animation:ovfade_0.2s_ease]"
      style={{ zIndex: z, background: `rgba(11,11,12,${String(backdrop)})` }}
    >
      <div
        role="dialog"
        aria-modal="true"
        onClick={(event) => {
          event.stopPropagation();
        }}
        className={`max-w-full rounded-[18px] bg-white [animation:fadeup_0.25s_ease] ${className}`}
      >
        {children}
      </div>
    </div>,
    document.body,
  );
}
