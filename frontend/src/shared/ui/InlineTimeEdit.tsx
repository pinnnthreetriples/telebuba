// Ported from Devigner UI, https://ui.devigner.cc/components/inline-time-edit — free to
// use, commercial use included. Restyled onto Telebuba's tokens; the motion is the
// original's: closed, three tiles butt together into one pill that reads `2:30`; opening
// splits them apart on a layout spring, the colon gives way to the units, each tile's
// shadow crossfades with the pill's, and the pen blurs into a tick.
//
// What changed in the port, and why:
//   - The pill is the `md` control height (36px) on `canvas`, with `radius.md` corners and
//     the hairline `ring` shadow; an open field is the numeric field's width (72px) and
//     takes the focused field's blue edge.
//   - The action tile is square at every state (the original grew it 36 → 40px open):
//     the dimension scale has no 40px rung, and one 4px step is not worth adding one.
//   - Values are medium weight rather than semibold — the app has two weights.
//   - Glyphs are the app's own `pencil` and `check` at their rungs, and the press
//     shrinks to the system's `pressScale.press` rather than 0.9.
//   - The `classNames` slots are gone: a closed design system does not hand them out.
import { AnimatePresence, motion, useReducedMotion } from 'motion/react';
import { useEffect, useRef, useState } from 'react';
import type { HTMLAttributes, RefObject } from 'react';

import { pressScale, radius, spring, tween } from '@/shared/design-system';
import { cn } from '@/shared/lib/cn';
import { useControllableState } from '@/shared/lib/useControllableState';

import { Icon } from './Icon';

const RADIUS = Number.parseFloat(radius.md);
const PRESS = Number(pressScale.press);

// Written out in full so Tailwind can see them: the same outline as `FOCUS_RING`, drawn
// by the pill (closed) or the action tile (open) for the button inside it. `visible` is
// the browser's own heuristic; `focus` is used once the keyboard is known to be driving,
// because a button focused quietly after a pointer save stays "not visible" through
// later key presses.
const RING_VISIBLE =
  'has-[button:focus-visible]:outline-solid has-[button:focus-visible]:outline-2 has-[button:focus-visible]:outline-offset-2 has-[button:focus-visible]:outline-action-primary';
const RING_FOCUS =
  'has-[button:focus]:outline-solid has-[button:focus]:outline-2 has-[button:focus]:outline-offset-2 has-[button:focus]:outline-action-primary';

/** Corner radii per tile. Closed, the three tiles butt together into one pill, so only
 *  the outer corners round; open, each tile is its own pill. */
function corners(open: boolean, edge: 'start' | 'middle' | 'end') {
  const start = open || edge === 'start' ? RADIUS : 0;
  const end = open || edge === 'end' ? RADIUS : 0;
  return {
    borderTopLeftRadius: start,
    borderBottomLeftRadius: start,
    borderTopRightRadius: end,
    borderBottomRightRadius: end,
  };
}

const digits = (raw: string) => raw.replace(/\D/g, '').slice(0, 2);

/** React's drag and animation handlers collide with Motion's own. */
type DivProps = Omit<
  HTMLAttributes<HTMLDivElement>,
  | 'defaultValue'
  | 'onChange'
  | 'onDrag'
  | 'onDragStart'
  | 'onDragEnd'
  | 'onAnimationStart'
  | 'onAnimationEnd'
  | 'onAnimationIteration'
>;

export interface InlineTimeEditProps extends DivProps {
  /** Controlled duration, in minutes. */
  value?: number;
  /** Initial duration in minutes when uncontrolled. */
  defaultValue?: number;
  /** Fires with the new total in minutes when a changed edit is saved. */
  onValueChange?: (minutes: number) => void;
  /** Controlled edit state. */
  open?: boolean;
  /** Initial edit state when uncontrolled. */
  defaultOpen?: boolean;
  /** Fires when the edit opens, saves or is discarded. */
  onOpenChange?: (open: boolean) => void;
  /** Largest hour value accepted. */
  maxHours?: number;
  /** Closed, show the time as a clock (2:30) instead of with its units (2 ч 30 мин).
   *  Editing always shows the units. */
  shortTime?: boolean;
  hourUnit?: string;
  minuteUnit?: string;
  /** Accessible names. */
  hoursLabel?: string;
  minutesLabel?: string;
  editLabel?: string;
  saveLabel?: string;
  disabled?: boolean;
}

/**
 * A duration that edits in place. Closed it reads `2:30` beside a pen; pressing it splits
 * the pill into an hours and a minutes field. Arrow keys step (Shift steps by ten), Enter
 * or the tick saves, Escape or leaving the control discards.
 */
export function InlineTimeEdit({
  value,
  defaultValue = 150,
  onValueChange,
  open: openProp,
  defaultOpen = false,
  onOpenChange,
  maxHours = 99,
  shortTime = true,
  hourUnit = 'ч',
  minuteUnit = 'мин',
  hoursLabel = 'Часы',
  minutesLabel = 'Минуты',
  editLabel = 'Изменить время',
  saveLabel = 'Сохранить время',
  disabled = false,
  className,
  style,
  ...rest
}: InlineTimeEditProps) {
  const [total, setTotal] = useControllableState({ value, defaultValue, onValueChange });
  const [open, setOpen] = useControllableState({
    value: openProp,
    defaultValue: defaultOpen,
    onValueChange: onOpenChange,
  });
  const reduced = useReducedMotion() ?? false;
  // Only what the user has typed. Untouched fields read from the value, so a controlled
  // value that changes mid-edit shows through.
  const [draft, setDraft] = useState<{ h?: string; m?: string }>({});
  // Whether the keyboard is driving the control; see `RING_FOCUS`.
  const [keyboard, setKeyboard] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const hoursRef = useRef<HTMLInputElement>(null);
  const minutesRef = useRef<HTMLInputElement>(null);
  const button = useRef<HTMLButtonElement>(null);

  const h = (open ? draft.h : undefined) ?? String(Math.floor(total / 60));
  const m = (open ? draft.m : undefined) ?? String(total % 60);
  // Closed, the time reads as a clock (2:05); open, the fields take bare numbers.
  const clock = shortTime && !open;
  const shownM = clock ? m.padStart(2, '0') : m;

  const start = (focus: HTMLElement | null) => {
    if (disabled) return;
    setDraft({});
    setOpen(true);
    focus?.focus();
  };
  /** `refocus` returns focus to the button; "quiet" does it without a focus ring, for a
   *  close that came from a pointer. */
  const close = (refocus: boolean | 'quiet') => {
    setDraft({});
    setOpen(false);
    // `focusVisible` is newer than TypeScript's DOM lib.
    if (refocus) button.current?.focus({ focusVisible: refocus !== 'quiet' } as FocusOptions);
  };
  // Saving the same total never fires onValueChange: the controllable state drops a
  // value equal to the current one.
  const save = (refocus: true | 'quiet' = true) => {
    const hours = Math.min(Number(h) || 0, maxHours);
    const minutes = Math.min(Number(m) || 0, 59);
    setTotal(hours * 60 + minutes);
    close(refocus);
  };

  // A new value from outside replaces the edit in progress. Returns the same object when
  // nothing is typed, so the mount run costs no render.
  useEffect(() => setDraft((d) => ((d.h ?? d.m) === undefined ? d : {})), [total]);
  // Otherwise re-enabling the control reopens an edit the user never raised.
  useEffect(() => {
    if (disabled) setOpen(false);
  }, [disabled, setOpen]);

  // Every layout node also takes layoutDependency={open}: Motion otherwise re-measures
  // all of them on every render (each keystroke), and only opening and closing animate.
  const layout = !reduced;
  const focusRing = keyboard ? RING_FOCUS : RING_VISIBLE;
  const fade = reduced ? { duration: 0 } : tween.fade;
  const tile = 'relative flex h-control shrink-0 items-center bg-canvas text-body';

  // Each tile carries its own shadow, shown only while the tiles stand apart. Closed,
  // butted shadows would draw seams through the pill, so the root draws one shadow for
  // the whole pill and the two crossfade.
  const tileShadow = (
    <motion.span
      aria-hidden="true"
      initial={false}
      animate={{ opacity: open ? 1 : 0 }}
      transition={fade}
      className="pointer-events-none absolute inset-0 rounded-[inherit] shadow-ring"
    />
  );

  const field = (
    edge: 'start' | 'middle',
    ref: RefObject<HTMLInputElement | null>,
    text: string,
    key: 'h' | 'm',
    label: string,
    unit: string,
  ) => {
    const max = key === 'h' ? maxHours : 59;
    const now = Math.min(Number(text) || 0, max);
    return (
      <motion.div
        layout={layout}
        layoutDependency={open}
        transition={spring.layout}
        // Closed, overlap a pixel: butted tiles at fractional widths leave a hairline seam
        // between their fills. Geometry of the seam, not a rung of the rhythm.
        style={{ ...corners(open, edge), marginRight: open ? 0 : -1 }}
        onPointerDown={(e) => {
          // A press anywhere on a tile lands in its field, opening the edit first.
          if (e.target === ref.current && open) return;
          e.preventDefault();
          if (open) ref.current?.focus();
          else start(ref.current);
        }}
        className={cn(
          tile,
          open
            ? 'w-number cursor-text justify-between px-3 has-[input:focus]:ring-1 has-[input:focus]:ring-action-primary'
            : cn(
                'cursor-pointer',
                edge === 'start' ? (clock ? 'pl-3' : 'pl-3 pr-1') : clock ? '' : 'pl-1',
              ),
        )}
      >
        {tileShadow}
        <motion.input
          ref={ref}
          layout={layout ? 'position' : false}
          layoutDependency={open}
          transition={spring.layout}
          type="text"
          inputMode="numeric"
          autoComplete="off"
          aria-label={label}
          readOnly={!open}
          tabIndex={open ? 0 : -1}
          value={text}
          onChange={(e) => {
            const next = digits(e.target.value);
            setDraft((d) => ({ ...d, [key]: next }));
          }}
          role="spinbutton"
          aria-valuemin={0}
          aria-valuemax={max}
          aria-valuenow={now}
          onKeyDown={(e) => {
            if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
              // Steps by 1, by 10 with Shift, clamped to the field's range.
              e.preventDefault();
              const step = (e.shiftKey ? 10 : 1) * (e.key === 'ArrowUp' ? 1 : -1);
              const next = Math.min(Math.max(now + step, 0), max);
              setDraft((d) => ({ ...d, [key]: String(next) }));
              return;
            }
            if (e.key !== 'Enter') return;
            // Save moves focus to the button; without this the same Enter lands on it as
            // a click and reopens the edit.
            e.preventDefault();
            save();
          }}
          // Sized to the digits: closed, the unit sits right against the value. Outline
          // inline: the app's unlayered :focus-visible rule outranks any utility, and the
          // tile draws the focus edge instead.
          style={{ width: `${String(Math.max(text.length, 1))}ch`, outline: 'none' }}
          className={cn(
            'relative min-w-0 bg-transparent font-medium tabular-nums text-content-primary caret-content-primary',
            !open && 'pointer-events-none',
          )}
        />
        <AnimatePresence initial={false} mode="popLayout">
          {!clock ? (
            <motion.span
              key="unit"
              layout={layout ? 'position' : false}
              layoutDependency={open}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ layout: spring.layout, opacity: fade }}
              aria-hidden="true"
              className="relative ml-1 select-none whitespace-nowrap text-content-muted"
            >
              {unit}
            </motion.span>
          ) : (
            edge === 'start' && (
              <motion.span
                key="colon"
                layout={layout ? 'position' : false}
                layoutDependency={open}
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ layout: spring.layout, opacity: fade }}
                aria-hidden="true"
                className="relative select-none font-medium text-content-primary"
              >
                :
              </motion.span>
            )
          )}
        </AnimatePresence>
      </motion.div>
    );
  };

  const hidden = reduced ? { opacity: 0 } : { opacity: 0, scale: 0.5, filter: 'blur(4px)' };

  return (
    <motion.div
      ref={root}
      layout={layout}
      layoutDependency={open}
      transition={spring.layout}
      role="group"
      style={{ borderRadius: RADIUS, ...style }}
      className={cn(
        'relative inline-flex w-max items-center',
        // Closed, the whole pill is the focus target; open, only the button's tile.
        open ? 'gap-2' : cn('gap-0', focusRing),
        disabled && 'pointer-events-none opacity-50',
        className,
      )}
      {...rest}
      onPointerDown={(e) => {
        rest.onPointerDown?.(e);
        setKeyboard(false);
        // The gaps between open tiles belong to the control; pressing one must not blur
        // the field and discard the edit.
        if (e.target === e.currentTarget) e.preventDefault();
      }}
      onBlur={(e) => {
        rest.onBlur?.(e);
        // Focus leaving the control (a click elsewhere, Tab past the button) discards the
        // edit, the same as Escape.
        if (open && !root.current?.contains(e.relatedTarget as Node | null)) close(false);
      }}
      onFocus={(e) => {
        rest.onFocus?.(e);
        // Tabbing in: the key press happened outside, before focus arrived.
        const target = e.target as Element;
        if (target === button.current && target.matches(':focus-visible')) setKeyboard(true);
      }}
      onKeyDown={(e) => {
        rest.onKeyDown?.(e);
        setKeyboard(true);
        if (e.key !== 'Escape' || !open) return;
        e.stopPropagation();
        close(true);
      }}
    >
      <motion.span
        aria-hidden="true"
        layout={layout}
        layoutDependency={open}
        transition={{ layout: spring.layout, opacity: fade }}
        initial={false}
        animate={{ opacity: open ? 0 : 1 }}
        style={{ borderRadius: RADIUS }}
        className="pointer-events-none absolute inset-0 shadow-ring"
      />
      {field('start', hoursRef, h, 'h', hoursLabel, hourUnit)}
      {field('middle', minutesRef, shownM, 'm', minutesLabel, minuteUnit)}
      <motion.div
        layout={layout}
        layoutDependency={open}
        transition={spring.layout}
        style={corners(open, 'end')}
        className={cn(tile, 'aspect-square', open && focusRing)}
      >
        {tileShadow}
        <motion.button
          ref={button}
          type="button"
          disabled={disabled}
          aria-label={open ? saveLabel : editLabel}
          onPointerDown={(e) => e.preventDefault()}
          onClick={(e) => {
            // `detail` is 0 for a click the keyboard made.
            const fromKeyboard = e.detail === 0;
            if (open) save(fromKeyboard ? true : 'quiet');
            else start(fromKeyboard ? button.current : hoursRef.current);
          }}
          whileTap={{ scale: PRESS, transition: spring.press }}
          style={{ outline: 'none' }}
          className="relative grid h-full w-full cursor-pointer place-items-center rounded-[inherit]"
        >
          <AnimatePresence initial={false} mode="popLayout">
            <motion.span
              key={open ? 'save' : 'edit'}
              initial={hidden}
              animate={{ opacity: 1, scale: 1, filter: 'blur(0px)' }}
              exit={hidden}
              transition={spring.swap}
              className="col-start-1 row-start-1 grid place-items-center"
            >
              {open ? (
                <span className="text-content-primary">
                  <Icon name="check" size={18} />
                </span>
              ) : (
                <span className="text-content-muted">
                  <Icon name="pencil" size={14} />
                </span>
              )}
            </motion.span>
          </AnimatePresence>
        </motion.button>
      </motion.div>
    </motion.div>
  );
}
