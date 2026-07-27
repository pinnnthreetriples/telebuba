import { type ReactNode, useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';

// Everything a keyboard can land on inside the dialog (for the Tab trap).
const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

// Mount order of open modals. Every Modal listens for Escape on `document`, so
// without this a nested dialog's Escape would also close its parent — only the
// topmost (last-registered) modal should handle it.
const modalStack: object[] = [];

// Overlay/card class pairs per shell shape. They are presets rather than two free
// `className` props because both halves set the same properties (radius, animation,
// height): letting a caller pass `rounded-none` alongside the base `rounded-[18px]`
// would depend on Tailwind's emit order, which is not a guarantee. `center` keeps
// the design's centred card; `drawer-left` is the mobile nav's flush side panel.
const SHELL = {
  center: {
    overlay: 'items-center justify-center p-4 sm:p-5',
    card: 'max-h-[calc(100dvh-2rem)] overflow-y-auto overscroll-contain rounded-[18px] [animation:fadeup_0.25s_ease]',
  },
  'drawer-left': {
    overlay: 'items-stretch justify-start',
    card: 'h-full overflow-y-auto overscroll-contain tb-drawerin',
  },
} as const;

// The design's modal shell: a fixed dimmed backdrop (ovfade) centering a white
// card (fadeup). Backdrop-click and Escape close; the card stops propagation.
// z and backdrop opacity match the design's per-modal values. Focus moves into
// the dialog on open, Tab cycles inside it, and the previously-focused element
// gets focus back on close.
export function Modal({
  onClose,
  children,
  className = 'w-[420px]',
  variant = 'center',
  z = 70,
  backdrop = 0.4,
}: {
  onClose: () => void;
  children: ReactNode;
  className?: string;
  variant?: keyof typeof SHELL;
  z?: number;
  backdrop?: number;
}) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const idRef = useRef<object>({});

  // Register in the modal stack for the lifetime of this dialog (mount/unmount
  // only) so the Escape handler can tell whether it is the topmost one.
  useEffect(() => {
    const id = idRef.current;
    modalStack.push(id);
    // The card scrolls internally, so the page behind it must not: on a phone a
    // scrollable body lets the backdrop drag away under the dialog. Restore only
    // when the last dialog leaves, so a nested one closing doesn't unlock early.
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      const index = modalStack.indexOf(id);
      if (index !== -1) modalStack.splice(index, 1);
      if (modalStack.length === 0) document.body.style.overflow = previousOverflow;
    };
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && modalStack[modalStack.length - 1] === idRef.current) {
        onClose();
      }
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
      className={`fixed inset-0 flex [animation:ovfade_0.2s_ease] ${SHELL[variant].overlay}`}
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
        className={`max-w-full bg-white outline-none ${SHELL[variant].card} ${className}`}
      >
        {children}
      </div>
    </div>,
    document.body,
  );
}
