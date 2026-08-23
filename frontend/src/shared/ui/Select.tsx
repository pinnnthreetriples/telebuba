import { useEffect, useRef, useState } from 'react';

export type SelectOption = { value: string; label: string; disabled?: boolean };

// Picking one item from a list, in the canon's own clothes. A native <select> is
// painted by the OS — its own font, border and arrow — so next to a design-system
// input in the same dialog it reads as a different application; the six sites that
// needed this each grew their own panel instead, with their own literal shadow.
const TRIGGER =
  'flex w-full items-center justify-between gap-[8px] rounded-lg border bg-white px-[13px] py-[9px] text-left text-[13px] text-ink outline-none hover:border-line-strong focus-visible:border-primary focus-visible:shadow-[0_0_0_3px_rgba(0,102,255,0.12)] disabled:cursor-default disabled:border-line disabled:bg-surface disabled:text-ink-subtle';
const OPTION =
  'flex w-full items-center justify-between gap-[8px] rounded-sm border-none px-[10px] py-[8px] text-left text-[12.5px] hover:bg-primary-tint disabled:text-ink-subtle';

export function Select({
  value,
  onChange,
  options,
  placeholder,
  disabled = false,
  ariaLabel,
  emptyLabel,
}: {
  value: string;
  onChange: (value: string) => void;
  options: SelectOption[];
  placeholder?: string;
  disabled?: boolean;
  ariaLabel?: string;
  emptyLabel?: string;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  // The label comes off the matching option, never off `value` being truthy: '' is a
  // real choice at two sites ("Все аккаунты", "Без медиа"), not the empty state.
  const current = options.find((option) => option.value === value);

  useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    return () => {
      document.removeEventListener('mousedown', onDown);
    };
  }, [open]);

  return (
    <div
      ref={rootRef}
      className="relative"
      onKeyDown={(event) => {
        // Escape closes the list and stops there: most of these sit inside a Modal,
        // whose own Escape listener is on `document`, and one key must not both pick
        // nothing and throw the dialog away. Only while the list is open — a closed
        // Select has no business swallowing the dialog's Escape.
        if (event.key === 'Escape' && open) {
          event.stopPropagation();
          setOpen(false);
        }
      }}
    >
      <button
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={ariaLabel}
        disabled={disabled}
        onClick={() => {
          setOpen((previous) => !previous);
        }}
        className={`${TRIGGER} ${open ? 'border-primary' : 'border-line'}`}
      >
        <span className={`min-w-0 truncate ${current ? '' : 'text-ink-subtle'}`}>
          {current?.label ?? placeholder}
        </span>
        <span className={`tb-ddchev flex shrink-0 text-ink-subtle ${open ? 'open' : ''}`}>
          <svg
            width="15"
            height="15"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
          >
            <path d="m6 9 6 6 6-6" />
          </svg>
        </span>
      </button>
      <div
        role="listbox"
        // .tb-dd collapses VISUALLY only (max-height:0 + opacity:0), so every option
        // below stays rendered and focusable while the list is closed — a keyboard
        // operator tabbed straight into an invisible list, and one sitting at the end
        // of a Modal's focusable list froze its Tab trap. `inert` is the real thing
        // and, unlike `hidden`, keeps the open/close transition.
        inert={!open}
        className={`tb-dd absolute inset-x-0 top-[calc(100%+5px)] z-pop rounded-lg border border-line bg-white p-1 shadow-pop ${open ? 'open' : ''}`}
      >
        {options.length === 0 ? (
          <div className="px-[10px] py-[8px] text-[12.5px] text-ink-subtle">{emptyLabel}</div>
        ) : (
          options.map((option) => (
            <button
              key={option.value}
              type="button"
              role="option"
              aria-selected={option.value === value}
              disabled={option.disabled}
              onClick={() => {
                onChange(option.value);
                setOpen(false);
              }}
              className={`${OPTION} ${option.value === value ? 'font-medium text-primary' : 'text-ink'}`}
            >
              <span className="min-w-0 truncate">{option.label}</span>
              {option.value === value ? (
                <svg
                  width="14"
                  height="14"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.4"
                  className="shrink-0"
                >
                  <path d="M20 6 9 17l-5-5" />
                </svg>
              ) : null}
            </button>
          ))
        )}
      </div>
    </div>
  );
}
