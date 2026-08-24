import { type ReactNode, useId, useLayoutEffect, useRef, useState } from 'react';

import { Icon } from './Icon';

// The design's collapsible accordion card: a header row (free-form content +
// chevron) over a max-height-collapsing body. Used across the account-edit,
// warming and neurocomment screens, which all share this pattern in the design.
function Chevron({ open }: { open: boolean }) {
  return (
    <span
      className={`flex text-ink-subtle transition-transform duration-reveal ease-spring ${open ? 'rotate-180' : ''}`}
    >
      <Icon name="chevron-down" size={16} />
    </span>
  );
}

export function CollapsibleCard({
  header,
  trailing,
  label,
  defaultOpen = false,
  onOpenChange,
  wrapperClassName = 'rounded-card border border-line bg-white',
  headerClassName = 'px-lg py-lg',
  bodyClassName = 'px-lg pb-lg',
  children,
}: {
  header: ReactNode;
  trailing?: ReactNode;
  label?: string;
  defaultOpen?: boolean;
  // Collapsing does NOT unmount the body (it only gets `hidden`), so a card
  // holding a one-time secret cannot rely on unmount to drop it. This tells the
  // owner the card just closed; the 2FA card clears its plaintext on it.
  onOpenChange?: (open: boolean) => void;
  wrapperClassName?: string;
  headerClassName?: string;
  bodyClassName?: string;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const collapseRef = useRef<HTMLDivElement>(null);
  const bodyId = useId();
  // Drop the max-height cap once the open transition settles, so content is
  // never clipped (the .tb-collapse `var(--mh)` cap is only for the animation).
  const [settled, setSettled] = useState(defaultOpen);
  // A collapsed body is only VISUALLY gone (max-height:0 + opacity:0), so
  // without this every control inside it stays in the tab order and the a11y
  // tree — a keyboard operator reached a hidden 2FA password field and a
  // "delete account" button. `hidden` goes back on only when the close
  // transition ends.
  //
  // On open, `hidden` comes off in the SAME React commit that adds `.tb-open`,
  // so the body goes from not-rendered to rendered with the open styles already
  // applied. An element that was not rendered has no before-change style, so
  // that step generates no transition on its own — measured in Chrome, the body
  // snapped straight to the CSS fallback `max-height: 600px` (clientHeight 600
  // against scrollHeight 976, `overflow: hidden`, no scrollbar) and only the
  // later `--mh` write animated. `@starting-style` in index.css supplies the
  // missing before-change style; do not replace it with a rAF that defers
  // `reachable` by a frame without re-measuring that.
  const [reachable, setReachable] = useState(defaultOpen);
  const toggle = () => {
    setSettled(false);
    setReachable(true);
    const next = !open;
    setOpen(next);
    onOpenChange?.(next);
  };

  // Drive the transition from the real content height so tall content (>600px)
  // isn't cut by the CSS fallback cap.
  useLayoutEffect(() => {
    const el = collapseRef.current;
    if (!el) return;
    if (open) {
      el.style.setProperty('--mh', `${String(el.scrollHeight)}px`);
    } else {
      el.style.removeProperty('--mh');
    }
  }, [open, children]);

  return (
    <div className={`overflow-hidden ${wrapperClassName}`}>
      <div className={`flex items-center gap-md ${headerClassName}`}>
        <button
          type="button"
          onClick={toggle}
          aria-expanded={open}
          aria-controls={bodyId}
          className="flex min-w-0 flex-1 items-center gap-md text-left"
        >
          {header}
        </button>
        {trailing}
        <button
          type="button"
          onClick={toggle}
          aria-label={label}
          aria-expanded={open}
          aria-controls={bodyId}
          className="flex shrink-0 items-center"
        >
          <Chevron open={open} />
        </button>
      </div>
      <div
        ref={collapseRef}
        id={bodyId}
        hidden={!reachable}
        className={`tb-collapse ${open ? 'tb-open' : ''} ${open && settled ? 'tb-settled' : ''}`}
        onTransitionEnd={(event) => {
          // React's handler bubbles: without this, any descendant transitioning
          // max-height drives THIS card's settled/reachable — and while closed that
          // means `hidden` on the whole body.
          if (event.target !== event.currentTarget) return;
          // Asymmetric on purpose, and this is a SHARED-layer fix: every
          // collapsible card in the app (account-edit, warming, neurocomment)
          // closes through this branch.
          //
          // The open ends on max-height (0 -> `--mh`, see the @starting-style note
          // in index.css). The close cannot: `.tb-settled` has by then dropped the
          // cap to `max-height: none`, and `none -> 0` is not interpolable, so
          // Chrome creates NO max-height transition for the close — only opacity
          // runs. Filtering the close on max-height therefore never ran
          // `setReachable(false)`, and after ONE open/close cycle every collapsed
          // body kept `hidden` off: measured in Chrome, `.focus()` on a control
          // inside a closed body succeeded and a nested input's value was
          // readable, i.e. exactly the a11y hole this state exists to close.
          // happy-dom fires no transitionend at all, which is why the suite could
          // not see it.
          if (open) {
            if (event.propertyName === 'max-height') setSettled(true);
          } else if (event.propertyName === 'opacity') {
            setReachable(false);
          }
        }}
      >
        <div className={bodyClassName}>{children}</div>
      </div>
    </div>
  );
}
