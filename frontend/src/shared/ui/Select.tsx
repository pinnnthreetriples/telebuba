import { useEffect, useId, useRef, useState } from 'react';

import { fieldBase, surface } from '@/shared/design-system';
import { cn } from '@/shared/lib/cn';

import { Icon } from './Icon';

export type SelectOption = { value: string; label: string; disabled?: boolean };

// Picking one item from a list, in the canon's own clothes. A native <select> is
// painted by the OS — its own font, border and arrow — so next to a design-system
// input in the same dialog it reads as a different application; the six sites that
// needed this each grew their own panel instead, with their own literal shadow.
// Триггер — то же поле, что у Input: высота, поля, рунг, форма и фокус приходят из
// `recipes/controls.ts`. До этого он стоял на `px-lg py-md` против `px-md py-md` у
// Input — четыре пикселя, которых никто не выбирал, — и был на 5px выше кнопки рядом.
//
// Гашение своё: недоступный ВЫБОР остаётся читаемым (в нём написано выбранное значение),
// поэтому он гасится заливкой и краской, а не прозрачностью, как кнопка.
const TRIGGER = cn(
  fieldBase({ size: 'md' }),
  'flex items-center justify-between gap-sm text-left text-content-primary',
  'border-line hover:border-line-strong focus-visible:border-focus focus-visible:shadow-focus',
  'disabled:cursor-default disabled:border-line disabled:bg-surface disabled:text-content-subtle',
);
const OPTION =
  'flex w-full items-center justify-between gap-sm rounded-sm border-none px-md py-sm text-left text-body hover:bg-action-hover disabled:text-content-subtle';

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
  // The keyboard cursor, as an index into `options`. DOM focus stays on the trigger the
  // whole time — the list is `inert` while closed, and moving real focus into it would
  // fight both that and the Modal Tab trap — so the cursor is published as
  // `aria-activedescendant` instead, pointing at the option ids minted below.
  //
  // That attribute only means something if the reference RESOLVES, and ARIA resolves it
  // in one of three places: a DOM descendant of the referring element, an `aria-owns`
  // logical descendant, or inside the element `aria-controls` names. The trigger is a
  // sibling of the list, so none of the three applied and the cursor pointed at nothing
  // — arrowing an open list announced the list once and then went silent. `listId` now
  // sits on the listbox itself and the trigger `aria-controls` it, which supplies the
  // third. `role="combobox"` is the other half: `aria-activedescendant` is only
  // supported on composite roles, and a `button` is not one of them, so the attribute
  // was being dropped for the role as well as for the dangling id. This is the APG
  // select-only combobox shape, and it costs the native button nothing — Enter and
  // Space still activate it, because a role never changes behaviour.
  const [active, setActive] = useState(-1);
  const rootRef = useRef<HTMLDivElement>(null);
  const listId = useId();
  const optionId = (index: number) => `${listId}-${String(index)}`;
  // The label comes off the matching option, never off `value` being truthy: '' is a
  // real choice at two sites ("Все аккаунты", "Без медиа"), not the empty state.
  const current = options.find((option) => option.value === value);

  // The next selectable option `delta` away, skipping disabled ones and WRAPPING at
  // both ends: these lists run 2–20 rows and the panel is a contained ring, the same
  // way Modal's own Tab trap wraps rather than dead-ending.
  const step = (from: number, delta: number): number => {
    const count = options.length;
    let next = from;
    for (let i = 0; i < count; i += 1) {
      next = (next + delta + count) % count;
      if (!options[next]?.disabled) return next;
    }
    return from;
  };

  const openList = () => {
    const chosen = options.findIndex((option) => option.value === value && !option.disabled);
    setActive(chosen === -1 ? step(-1, 1) : chosen);
    setOpen(true);
  };

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

  // `.tb-dd.open` is a 240px scroller and these lists run to 20 rows, so past the sixth
  // option the cursor is a highlight on a row nobody can see — and because DOM focus
  // never enters the list, the browser's own scroll-focus-into-view never fires either.
  // `block: 'nearest'` scrolls the minimum and does nothing while the row is already in
  // view, and there is no `behavior: 'smooth'` here, so this adds no motion to exempt
  // under `prefers-reduced-motion`.
  useEffect(() => {
    if (!open || active < 0) return;
    document.getElementById(`${listId}-${String(active)}`)?.scrollIntoView({ block: 'nearest' });
  }, [open, active, listId]);

  return (
    <div
      ref={rootRef}
      className="relative"
      onKeyDown={(event) => {
        const { key } = event;
        // Escape closes the list and stops there: most of these sit inside a Modal,
        // whose own Escape listener is on `document`, and one key must not both pick
        // nothing and throw the dialog away. Only while the list is open — a closed
        // Select has no business swallowing the dialog's Escape.
        if (key === 'Escape') {
          if (!open) return;
          event.stopPropagation();
          setOpen(false);
          return;
        }
        if (key === 'ArrowDown' || key === 'ArrowUp') {
          event.preventDefault();
          if (open) setActive((from) => step(from, key === 'ArrowDown' ? 1 : -1));
          else openList();
          return;
        }
        // Below here the keys only mean something to an open list; closed, Enter and
        // Space are the button's own activation and must stay that way.
        if (!open) return;
        if (key === 'Home' || key === 'End') {
          event.preventDefault();
          setActive(key === 'Home' ? step(-1, 1) : step(0, -1));
        } else if (key === 'Enter' || key === ' ') {
          event.preventDefault();
          const chosen = options[active];
          if (chosen && !chosen.disabled) {
            onChange(chosen.value);
            setOpen(false);
          }
        }
      }}
    >
      <button
        type="button"
        role="combobox"
        aria-haspopup="listbox"
        aria-controls={listId}
        aria-expanded={open}
        aria-activedescendant={open && active >= 0 ? optionId(active) : undefined}
        aria-label={ariaLabel}
        disabled={disabled}
        onClick={() => {
          if (open) setOpen(false);
          else openList();
        }}
        className={`${TRIGGER} ${open ? 'border-action-primary' : 'border-line'}`}
      >
        <span className={`min-w-0 truncate ${current ? '' : 'text-content-subtle'}`}>
          {current?.label ?? placeholder}
        </span>
        <span className={`tb-ddchev flex shrink-0 text-content-subtle ${open ? 'open' : ''}`}>
          <Icon name="chevron-down" size={16} />
        </span>
      </button>
      <div
        id={listId}
        role="listbox"
        // .tb-dd collapses VISUALLY only (max-height:0 + opacity:0), so every option
        // below stays rendered and focusable while the list is closed — a keyboard
        // operator tabbed straight into an invisible list, and one sitting at the end
        // of a Modal's focusable list froze its Tab trap. `inert` is the real thing
        // and, unlike `hidden`, keeps the open/close transition.
        inert={!open}
        className={cn(
          'tb-dd absolute inset-x-0 top-[calc(100%+5px)] z-pop p-xs',
          surface('panel'),
          open && 'open',
        )}
      >
        {options.length === 0 ? (
          <div className="px-md py-sm text-body text-content-subtle">{emptyLabel}</div>
        ) : (
          options.map((option, index) => (
            <button
              key={option.value}
              id={optionId(index)}
              type="button"
              role="option"
              aria-selected={option.value === value}
              // `inert` keeps a keyboard operator out of the CLOSED list, and comes off
              // the moment it opens — so without this an open twenty-row list put twenty
              // tab stops between the trigger and the next control, in a list whose whole
              // keyboard contract is that DOM focus never leaves the trigger. A row is
              // reached with the arrows and committed with Enter; Tab belongs to the
              // trigger in both states.
              tabIndex={-1}
              disabled={option.disabled}
              onClick={() => {
                onChange(option.value);
                setOpen(false);
              }}
              className={`${OPTION} ${option.value === value ? 'font-medium text-info-strong' : 'text-content-primary'} ${
                open && index === active ? 'bg-info-tint' : ''
              }`}
            >
              <span className="min-w-0 truncate">{option.label}</span>
              {option.value === value ? <Icon name="check" size={14} className="shrink-0" /> : null}
            </button>
          ))
        )}
      </div>
    </div>
  );
}
