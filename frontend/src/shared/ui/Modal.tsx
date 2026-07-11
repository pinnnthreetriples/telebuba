import { type ReactNode, useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';

// Everything a keyboard can land on inside the dialog (for the Tab trap).
const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

// The design's modal shell: a fixed dimmed backdrop (ovfade) centering a white
// card (fadeup). Backdrop-click and Escape close; the card stops propagation.
// z and backdrop opacity match the design's per-modal values. Focus moves into
// the dialog on open, Tab cycles inside it, and the previously-focused element
// gets focus back on close.
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
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('keydown', onKey);
    };
  }, [onClose]);

  // Focus the dialog on open; hand focus back to the opener on close.
  useEffect(() => {
    const previous = document.activeElement;
    dialogRef.current?.focus();
    return () => {
      if (previous instanceof HTMLElement) previous.focus();
    };
  }, []);

  // Minimal Tab trap: wrap from the last focusable to the first and back.
  const onTrapTab = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== 'Tab') return;
    const node = dialogRef.current;
    if (!node) return;
    const focusables = node.querySelectorAll<HTMLElement>(FOCUSABLE);
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    if (!first || !last) return;
    if (event.shiftKey && (document.activeElement === first || document.activeElement === node)) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  return createPortal(
    <div
      role="presentation"
      onClick={onClose}
      className="fixed inset-0 flex items-center justify-center p-5 [animation:ovfade_0.2s_ease]"
      style={{ zIndex: z, background: `rgba(11,11,12,${String(backdrop)})` }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        tabIndex={-1}
        onKeyDown={onTrapTab}
        onClick={(event) => {
          event.stopPropagation();
        }}
        className={`max-w-full rounded-[18px] bg-white outline-none [animation:fadeup_0.25s_ease] ${className}`}
      >
        {children}
      </div>
    </div>,
    document.body,
  );
}
