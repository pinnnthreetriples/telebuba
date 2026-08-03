import { type ReactNode, useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';

// Everything a keyboard can land on inside the dialog (for the Tab trap).
const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

// Mount order of open modals. Every Modal listens for Escape on `document`, so
// without this a nested dialog's Escape would also close its parent — only the
// topmost (last-registered) modal should handle it.
const modalStack: object[] = [];

// body.overflow as it was before the FIRST dialog locked it. Module-level, not
// per-instance: a second dialog opening over the first captures 'hidden', because the
// first already wrote it. Whichever instance restores LAST then decides the final
// value, and cleanup order follows document order, so a confirm rendered as the later
// sibling of its parent dialog (ProfileModal's discard-changes confirm) restores
// 'hidden' and leaves the page permanently unscrollable.
// Note it is the SIBLING case that bites. A dialog genuinely nested in another's
// `children` would be fine on its own — mount effects run child-first, so it captures
// the real value before its parent locks anything.
// Gating both the capture and the restore on an empty stack makes the ordering
// irrelevant: only the true 0→1 and 1→0 transitions touch this.
let overflowBeforeLock = '';

// Presets rather than two free `className` props: both halves set the same
// properties (radius, animation, height), and a caller's `rounded-none` beside the
// base `rounded-[18px]` would depend on Tailwind's emit order, which is no guarantee.
//
// A card taller than the viewport scrolls via the OVERLAY, never via the card. Both
// alternatives are wrong: `overflow-y-auto` on the card computes `overflow-x` to
// `auto` as well, so it clips absolutely-positioned children meant to escape it
// (ListenerEditModal's dropdown; WarmDaysModal's nowrap `.tb-tip-pop`, ~1000px wide at
// opacity 0, which turns into a permanent horizontal scrollbar) — and no cap at all
// leaves a tall dialog clipped at BOTH ends with nothing to scroll, since the overlay
// is `fixed` and `body.overflow` is locked while it is open.
// `m-auto` on the card rather than `items-center` on the overlay: centring a flex item
// with `align-items` makes the overflowing top unreachable once the container scrolls,
// whereas auto margins centre it and still yield to the scroll.
const SHELL = {
  center: {
    overlay: 'justify-center overflow-y-auto overscroll-contain p-4 sm:p-5',
    card: 'm-auto rounded-[18px] [animation:fadeup_0.25s_ease]',
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
  label,
  z = 70,
  backdrop = 0.4,
}: {
  onClose: () => void;
  children: ReactNode;
  className?: string;
  variant?: keyof typeof SHELL;
  // Accessible name for the dialog — REQUIRED, not optional: while it was
  // optional 20 of the 21 call sites left it out and a screen reader announced a
  // nameless "dialog". Every one of them already renders a title; pass that.
  label: string;
  z?: number;
  backdrop?: number;
}) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const idRef = useRef<object>({});

  // Register in the modal stack for the lifetime of this dialog (mount/unmount
  // only) so the Escape handler can tell whether it is the topmost one.
  useEffect(() => {
    const id = idRef.current;
    // The overlay scrolls, so the page behind it must not: on a phone a scrollable
    // body lets the backdrop drag away under the dialog, and a nested scroll chain
    // hands the overscroll to the page. Only the first dialog locks and only the last
    // unlocks, so a second one closing can't unlock early.
    if (modalStack.length === 0) overflowBeforeLock = document.body.style.overflow;
    modalStack.push(id);
    document.body.style.overflow = 'hidden';
    return () => {
      const index = modalStack.indexOf(id);
      if (index !== -1) modalStack.splice(index, 1);
      if (modalStack.length === 0) document.body.style.overflow = overflowBeforeLock;
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
        aria-label={label}
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
