// Ported from Devigner UI, https://ui.devigner.cc/components/date-range-picker — free to
// use, commercial use included. Restyled onto Telebuba's tokens; the motion is the
// original's: the check-in and check-out circles glide between days on a layout spring,
// the range band grows out of its fixed end and winds back into it, a hovered day
// previews the range before it is committed, the field underline slides between
// check-in and check-out, and dates, month and badge roll up when they change.
//
// What changed in the port, and why:
//   - Russian throughout: Monday-first weekdays, month names and dates from `Intl` for
//     `ru-RU`, «3 ночи»/«5 ночей» by plural rule, presets «Выходные», «3 ночи»,
//     «1 неделя», «2 недели».
//   - The selected circles are the action blue and the band is the selection tint — the
//     app's colours for "this is chosen" — instead of the original's ink and grey.
//   - Day circles are the `tile` square (34px) in rows of the same height, 4px apart, so
//     the band runs flush into a circle; the original's 36px circles sat in 40px rows.
//   - Presses shrink to the system's `pressScale.press`; the badge rolls on the `roll`
//     tween (`swap` on `out`) and the band follows the pointer on `follow` (`state`).
//   - The field underline spans the field's text rather than a fixed 36px — the dimension
//     scale has no such rung, and the nav underline the app already draws spans its tab.
//   - `today` is a prop (default: now), so a test or a static page can pin it.
//   - The `classNames`/`slotProps` restyling slots are gone.
import { AnimatePresence, LayoutGroup, motion, useReducedMotion } from 'motion/react';
import type { Transition } from 'motion/react';
import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react';
import type { HTMLAttributes, Key, KeyboardEvent, ReactNode } from 'react';

import { FOCUS_RING, pressScale, size, spring, tween } from '@/shared/design-system';
import { cn } from '@/shared/lib/cn';
import { useControllableState } from '@/shared/lib/useControllableState';
import {
  addDays,
  addMonths,
  clampRangeEnd,
  compareDays,
  firstEnabledDayInMonth,
  getMonthGrid,
  isSameDay,
  isWithinRange,
  nightsBetween,
  startOfDay,
  startOfMonth,
  type DateRange,
  type MonthGridDay,
} from '@/shared/lib/dateRange';

import { DEFAULT_DATE_RANGE_PRESETS, type DateRangePreset } from './dateRangePresets';
import { Icon } from './Icon';

const LOCALE = 'ru-RU';
const PRESS = Number(pressScale.press);
const INSTANT = { duration: 0 } as const;

// How far into the hovered day a preview band reaches, in columns from the day's near
// edge: past a two-digit number and still inside the hover circle, so the band's end is
// never seen and the two read as one shape.
const HOVER_REACH = 0.75;
// Half the band's height — the band is a row, and a row is a day circle tall — so a
// capped row end is a true semicircle. Numeric so Motion can animate it.
const FILL_RADIUS = Number.parseFloat(size.tile) / 2;
// How long the badge sits on a new value before showing it, the way a tooltip waits
// before opening: sweeping across days then reads as one settled change instead of a
// counter flickering through every number. A threshold, not a motion rung.
const BADGE_DELAY_MS = 120;

const capitalize = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);
const formatMonthName = (date: Date) =>
  capitalize(date.toLocaleDateString(LOCALE, { month: 'long' }));
const formatMonthLabel = (date: Date) => `${formatMonthName(date)} ${String(date.getFullYear())}`;
const formatShortDate = (date: Date) =>
  date.toLocaleDateString(LOCALE, { day: 'numeric', month: 'short' });
const formatFullDate = (date: Date) =>
  date.toLocaleDateString(LOCALE, { day: 'numeric', month: 'long' });
const NIGHT_FORMS: Partial<Record<Intl.LDMLPluralRule, string>> = { one: 'ночь', few: 'ночи' };
const nightRule = new Intl.PluralRules(LOCALE);
const formatNights = (nights: number) =>
  `${String(nights)} ${NIGHT_FORMS[nightRule.select(nights)] ?? 'ночей'}`;

// Monday first. 1 January 2024 was a Monday.
const WEEKDAYS = Array.from({ length: 7 }, (_, i) => {
  const date = new Date(2024, 0, 1 + i);
  return {
    short: capitalize(date.toLocaleDateString(LOCALE, { weekday: 'short' })),
    full: date.toLocaleDateString(LOCALE, { weekday: 'long' }),
  };
});

const HOVER_QUERY = '(hover: hover) and (pointer: fine)';
const subscribeHover = (onChange: () => void) => {
  if (typeof window.matchMedia !== 'function') return () => undefined;
  const query = window.matchMedia(HOVER_QUERY);
  query.addEventListener('change', onChange);
  return () => query.removeEventListener('change', onChange);
};
/** Whether the pointer can hover. On touch, a "hover" preview would stick to the last
 *  tapped day, so the preview is a pointer-only affordance. */
function useHoverCapable(): boolean {
  return useSyncExternalStore(
    subscribeHover,
    () => typeof window.matchMedia === 'function' && window.matchMedia(HOVER_QUERY).matches,
    () => false,
  );
}

/** `value` and its `key`, held back by `delay` ms whenever `key` changes. */
function useLazyValue<T>(value: T, key: Key, delay: number): { key: Key; value: T } {
  const [shown, setShown] = useState<{ key: Key; value: T }>({ key, value });
  const latest = useRef(value);
  latest.current = value;
  useEffect(() => {
    if (key === shown.key) return;
    if (delay <= 0) {
      setShown({ key, value: latest.current });
      return;
    }
    const timer = setTimeout(() => setShown({ key, value: latest.current }), delay);
    return () => clearTimeout(timer);
  }, [key, shown.key, delay]);
  return key === shown.key || delay <= 0 ? { key, value } : shown;
}

export interface DateRangePickerProps extends Omit<
  HTMLAttributes<HTMLDivElement>,
  'defaultValue' | 'title'
> {
  value?: DateRange | null;
  defaultValue?: DateRange | null;
  onValueChange?: (range: DateRange | null) => void;
  /** The month on screen. */
  month?: Date;
  defaultMonth?: Date;
  onMonthChange?: (month: Date) => void;
  minDate?: Date;
  maxDate?: Date;
  isDateDisabled?: (date: Date) => boolean;
  /** The day marked as today and the anchor of a preset with nothing picked yet. */
  today?: Date;
  title?: ReactNode;
  /** Defaults to «Выходные», «3 ночи», «1 неделя», «2 недели». Pass `[]` to hide. */
  presets?: DateRangePreset[];
  startLabel?: string;
  endLabel?: string;
  clearLabel?: string;
  /** Replaces the badge text. Receives the committed range, or the hover preview while a
   *  check-out is being picked. */
  renderBadge?: (range: DateRange | null) => ReactNode;
  onClear?: () => void;
}

/** Text that rolls up when it changes: the old value leaves upward while the new one
 *  rises in from below. */
function RollingText({
  id,
  reduced,
  transition = spring.swap,
  className,
  children,
}: {
  id: Key;
  reduced: boolean;
  transition?: Transition;
  className?: string;
  children: ReactNode;
}) {
  if (reduced) return <span className={cn('block', className)}>{children}</span>;
  return (
    <span className="relative block">
      <AnimatePresence mode="popLayout" initial={false}>
        <motion.span
          key={id}
          className={cn('block whitespace-nowrap', className)}
          initial={{ y: '100%', opacity: 0 }}
          animate={{ y: 0, opacity: 1 }}
          exit={{ y: '-100%', opacity: 0 }}
          transition={transition}
        >
          {children}
        </motion.span>
      </AnimatePresence>
    </span>
  );
}

function NavButton({
  label,
  reduced,
  disabled,
  onClick,
  children,
}: {
  label: string;
  reduced: boolean;
  disabled: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <motion.button
      type="button"
      aria-label={label}
      disabled={disabled}
      onClick={onClick}
      whileTap={reduced ? undefined : { scale: PRESS }}
      transition={spring.press}
      className={cn(
        'grid size-icon cursor-pointer place-items-center rounded-full text-content-muted transition-colors hover:bg-canvas hover:text-content-primary disabled:pointer-events-none disabled:opacity-50',
        FOCUS_RING,
      )}
    >
      {children}
    </motion.button>
  );
}

function Field({
  label,
  date,
  active,
  reduced,
  onClick,
}: {
  label: string;
  date: Date | null;
  active: boolean;
  reduced: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn('inline-flex cursor-pointer flex-col rounded-sm text-left', FOCUS_RING)}
    >
      <span className="block type-small-medium">{label}</span>
      <RollingText
        id={date ? date.getTime() : 'empty'}
        reduced={reduced}
        className={cn(
          'mt-1 text-h3',
          date ? 'font-medium text-content-primary' : 'font-normal text-content-subtle',
        )}
      >
        {date ? formatShortDate(date) : 'Добавить дату'}
      </RollingText>
      <span className="mt-2 block h-rail w-full">
        {active && (
          <motion.span
            layoutId="underline"
            transition={reduced ? INSTANT : spring.layout}
            className="block h-full w-full rounded-full bg-action-primary"
          />
        )}
      </span>
    </button>
  );
}

/**
 * A calendar for a check-in/check-out range. The first day pressed is the check-in; while
 * the check-out is being picked, hovering a day previews the range. Presets fill a range
 * from the check-in (or today), «Сбросить» clears it. Arrow keys, Home/End and
 * PageUp/PageDown move through the days, skipping disabled ones.
 */
export function DateRangePicker({
  value: controlledValue,
  defaultValue = null,
  onValueChange,
  month: controlledMonth,
  defaultMonth,
  onMonthChange,
  minDate,
  maxDate,
  isDateDisabled,
  today: todayProp,
  title = 'Даты поездки',
  presets = DEFAULT_DATE_RANGE_PRESETS,
  startLabel = 'Заезд',
  endLabel = 'Выезд',
  clearLabel = 'Сбросить',
  renderBadge,
  onClear,
  className,
  ...rest
}: DateRangePickerProps) {
  const today = useMemo(() => startOfDay(todayProp ?? new Date()), [todayProp]);
  const [value, setValue] = useControllableState<DateRange | null>({
    value: controlledValue,
    defaultValue,
    onValueChange,
  });
  const [month, setMonth] = useControllableState<Date>({
    value: controlledMonth,
    defaultValue: startOfMonth(defaultMonth ?? value?.start ?? today),
    onValueChange: onMonthChange,
  });
  const isDisabled = useCallback(
    (date: Date) => {
      if (minDate && date < startOfDay(minDate)) return true;
      if (maxDate && date > startOfDay(maxDate)) return true;
      return isDateDisabled?.(date) ?? false;
    },
    [minDate, maxDate, isDateDisabled],
  );
  const [pendingStart, setPendingStart] = useState<Date | null>(null);
  const [hoverDate, setHoverDate] = useState<Date | null>(null);
  const canHover = useHoverCapable();
  const reduced = useReducedMotion() ?? false;
  // Per instance: shared layoutIds would make two pickers on one page trade circles.
  const instanceId = useId();
  const [focusedDate, setFocusedDate] = useState<Date>(() => value?.start ?? today);
  const dayRefs = useRef(new Map<number, HTMLButtonElement>());
  // Only keyboard navigation moves DOM focus; mount and pointer focus only update state,
  // so the roving tabindex stays in sync without yanking focus around.
  const focusFromKeyboard = useRef(false);
  useEffect(() => {
    if (!focusFromKeyboard.current) return;
    focusFromKeyboard.current = false;
    dayRefs.current.get(startOfDay(focusedDate).getTime())?.focus();
  }, [focusedDate]);

  const moveFocus = (from: Date, days: number) => {
    const direction = days < 0 ? -1 : 1;
    let next = addDays(from, days);
    for (let attempts = 0; isDisabled(next) && attempts < 42; attempts++) {
      next = addDays(next, direction);
    }
    if (isDisabled(next)) return;
    if (next.getMonth() !== month.getMonth() || next.getFullYear() !== month.getFullYear()) {
      setMonth(startOfMonth(next));
    }
    focusFromKeyboard.current = true;
    setFocusedDate(next);
  };
  const moveFocusMonths = (from: Date, months: number) => {
    const next = addMonths(from, months);
    const length = new Date(next.getFullYear(), next.getMonth() + 1, 0).getDate();
    next.setDate(Math.min(from.getDate(), length));
    const target = isDisabled(next) ? firstEnabledDayInMonth(next, isDisabled) : next;
    if (!target) return;
    setMonth(startOfMonth(target));
    focusFromKeyboard.current = true;
    setFocusedDate(target);
  };
  const handleDayKeyDown = (date: Date, event: KeyboardEvent) => {
    const weekday = (date.getDay() + 6) % 7;
    const moves: Record<string, () => void> = {
      ArrowRight: () => moveFocus(date, 1),
      ArrowLeft: () => moveFocus(date, -1),
      ArrowDown: () => moveFocus(date, 7),
      ArrowUp: () => moveFocus(date, -7),
      Home: () => moveFocus(date, -weekday),
      End: () => moveFocus(date, 6 - weekday),
      PageUp: () => moveFocusMonths(date, -1),
      PageDown: () => moveFocusMonths(date, 1),
    };
    const move = moves[event.key];
    if (!move) return;
    event.preventDefault();
    move();
  };

  const liveMessage = value
    ? `${startLabel} ${formatFullDate(value.start)}, ${endLabel.toLowerCase()} ${formatFullDate(value.end)}, ${formatNights(nightsBetween(value.start, value.end))}`
    : '';

  /** A preset's range from `anchor`, as local days, ordered, and clamped at the first
   *  disabled day. Null when it would start on a disabled day. */
  const resolvePreset = (preset: DateRangePreset, anchor: Date): DateRange | null => {
    const raw = preset.getRange(anchor);
    let start = startOfDay(raw.start);
    let end = startOfDay(raw.end);
    if (compareDays(start, end) > 0) [start, end] = [end, start];
    if (isDisabled(start)) return null;
    return { start, end: clampRangeEnd(start, end, isDisabled) };
  };
  // A preset lights up whenever the committed range is exactly what it would produce from
  // that range's check-in, however the range was picked.
  const isPresetActive = (preset: DateRangePreset) => {
    if (!value) return false;
    const range = resolvePreset(preset, value.start);
    return !!range && isSameDay(range.start, value.start) && isSameDay(range.end, value.end);
  };
  const applyPreset = (preset: DateRangePreset) => {
    const range = resolvePreset(preset, value?.start ?? pendingStart ?? today);
    if (!range) return;
    setValue(range);
    const rangeMonth = startOfMonth(range.start);
    if (rangeMonth.getTime() !== month.getTime()) setMonth(rangeMonth);
    setPendingStart(null);
    setHoverDate(null);
  };
  const reset = () => {
    setValue(null);
    setPendingStart(null);
    setHoverDate(null);
  };
  const pickCheckOut = () => {
    if (pendingStart || !value) return;
    setPendingStart(value.start);
    setHoverDate(null);
  };
  const handleDayClick = (date: Date) => {
    if (isDisabled(date)) return;
    if (pendingStart === null) {
      if (value !== null) setValue(null);
      setPendingStart(date);
      return;
    }
    const ordered =
      compareDays(date, pendingStart) < 0
        ? { start: date, end: pendingStart }
        : { start: pendingStart, end: date };
    setValue({ start: ordered.start, end: clampRangeEnd(ordered.start, ordered.end, isDisabled) });
    setPendingStart(null);
    setHoverDate(null);
  };

  const previewEnd =
    pendingStart && hoverDate && canHover
      ? clampRangeEnd(pendingStart, hoverDate, isDisabled)
      : null;
  // A pending start wins over `value`, so a controlled parent that only accepts complete
  // ranges still sees the first click reflected.
  const previewOther = previewEnd ?? pendingStart;
  const displayRange: DateRange | null =
    pendingStart && previewOther
      ? compareDays(pendingStart, previewOther) <= 0
        ? { start: pendingStart, end: previewOther }
        : { start: previewOther, end: pendingStart }
      : value;
  const headerStart = pendingStart ?? value?.start ?? null;
  const headerEnd = pendingStart ? null : (value?.end ?? null);
  const startCircle = pendingStart ?? value?.start ?? null;
  const endCircle = pendingStart ? null : (value?.end ?? null);
  const spansNights = !!displayRange && !isSameDay(displayRange.start, displayRange.end);
  const badgeRange: DateRange | null = pendingStart ? (spansNights ? displayRange : null) : value;
  const badgeText = renderBadge
    ? renderBadge(badgeRange)
    : badgeRange
      ? formatNights(nightsBetween(badgeRange.start, badgeRange.end))
      : pendingStart
        ? `Выберите ${endLabel.toLowerCase()}`
        : 'Добавьте даты';
  const badgeKey = badgeRange
    ? `range-${String(nightsBetween(badgeRange.start, badgeRange.end))}`
    : pendingStart
      ? 'pick'
      : 'empty';
  const badge = useLazyValue(
    { text: badgeText, accent: !!badgeRange },
    badgeKey,
    reduced ? 0 : BADGE_DELAY_MS,
  );

  // The band grows out of, and retracts back into, the fixed end of the range. A clear
  // has no fixed end, so it collapses into the middle.
  const lastRange = useRef<DateRange | null>(null);
  if (displayRange && spansNights) lastRange.current = displayRange;
  const enterAnchor = (pendingStart ?? displayRange?.start ?? today).getTime();
  const exitAnchor = pendingStart
    ? pendingStart.getTime()
    : lastRange.current
      ? (lastRange.current.start.getTime() + lastRange.current.end.getTime()) / 2
      : 0;

  const weeks = useMemo(() => getMonthGrid(month), [month]);
  const tabbableDate = useMemo(() => {
    const visible =
      focusedDate.getMonth() === month.getMonth() &&
      focusedDate.getFullYear() === month.getFullYear();
    if (visible && !isDisabled(focusedDate)) return focusedDate;
    return firstEnabledDayInMonth(month, isDisabled);
  }, [focusedDate, month, isDisabled]);
  const monthTime = startOfMonth(month).getTime();
  const atMinMonth = minDate !== undefined && monthTime <= startOfMonth(minDate).getTime();
  const atMaxMonth = maxDate !== undefined && monthTime >= startOfMonth(maxDate).getTime();

  return (
    <div
      className={cn(
        // eslint-disable-next-line design-tokens/no-raw-values -- the calendar's own width, the original's: four presets and «Сбросить» fit one row, seven ~53px day columns. One component's internal layout, not a rung (that `width.confirm` is also 420px is a coincidence of value, not of role).
        'flex w-[420px] max-w-full flex-col rounded-lg bg-canvas p-2 shadow-pop',
        className,
      )}
      {...rest}
    >
      <div className="flex h-touch items-center justify-between gap-3 px-3">
        <div className="truncate type-body-medium">{title}</div>
        <motion.div
          layout
          transition={reduced ? INSTANT : tween.roll}
          className={cn(
            'flex h-bar shrink-0 items-center overflow-hidden rounded-full px-2 text-small font-medium transition-colors duration-swap',
            badge.value.accent
              ? 'bg-info-tint text-info-strong'
              : 'bg-surface-card text-content-muted',
          )}
        >
          <motion.span
            layout="position"
            transition={reduced ? INSTANT : tween.roll}
            className="block"
          >
            <RollingText id={badge.key} reduced={reduced} transition={tween.roll}>
              {badge.value.text}
            </RollingText>
          </motion.span>
        </motion.div>
      </div>
      <div className="rounded-md bg-surface-card p-4 shadow-seg">
        <LayoutGroup id={instanceId}>
          <div className="grid grid-cols-2 border-b border-line pb-4">
            <div>
              <Field
                label={startLabel}
                date={headerStart}
                active={!pendingStart}
                reduced={reduced}
                onClick={reset}
              />
            </div>
            <div className="border-l border-line pl-4">
              <Field
                label={endLabel}
                date={headerEnd}
                active={!!pendingStart}
                reduced={reduced}
                onClick={pickCheckOut}
              />
            </div>
          </div>
          <div className="flex items-center justify-between pt-4">
            <div className="text-h3 font-medium text-content-primary">
              <RollingText id={monthTime} reduced={reduced}>
                {formatMonthName(month)}{' '}
                <span className="text-content-subtle">{month.getFullYear()}</span>
              </RollingText>
            </div>
            <div className="flex items-center gap-1">
              <NavButton
                label="Предыдущий месяц"
                reduced={reduced}
                disabled={atMinMonth}
                onClick={() => setMonth((m) => addMonths(m, -1))}
              >
                <Icon name="chevron-left" size={16} />
              </NavButton>
              <NavButton
                label="Следующий месяц"
                reduced={reduced}
                disabled={atMaxMonth}
                onClick={() => setMonth((m) => addMonths(m, 1))}
              >
                <Icon name="chevron-right" size={16} />
              </NavButton>
            </div>
          </div>
          <div role="grid" aria-label={formatMonthLabel(month)} className="mt-4 select-none">
            <div role="row" className="grid grid-cols-7 text-center type-small">
              {WEEKDAYS.map((day) => (
                <div key={day.full} role="columnheader" aria-label={day.full}>
                  {day.short}
                </div>
              ))}
            </div>
            <div className="mt-2 flex flex-col gap-1">
              {weeks.map((week) => (
                <div
                  role="row"
                  key={week[0]?.date.getTime()}
                  className="relative grid h-tile grid-cols-7"
                >
                  <RowFill
                    week={week}
                    range={spansNights ? displayRange : null}
                    circleStart={
                      !!displayRange &&
                      [startCircle, endCircle].some((d) => d && isSameDay(d, displayRange.start))
                    }
                    circleEnd={
                      !!displayRange &&
                      [startCircle, endCircle].some((d) => d && isSameDay(d, displayRange.end))
                    }
                    hovered={
                      previewEnd && pendingStart && !isSameDay(previewEnd, pendingStart)
                        ? previewEnd
                        : null
                    }
                    enterAnchor={enterAnchor}
                    exitAnchor={exitAnchor}
                    reduced={reduced}
                  />
                  {week.map((cell) => {
                    const circle =
                      startCircle && isSameDay(cell.date, startCircle)
                        ? 'start'
                        : endCircle && isSameDay(cell.date, endCircle)
                          ? 'end'
                          : null;
                    return (
                      <DayCell
                        key={cell.date.getTime()}
                        cell={cell}
                        disabled={isDisabled(cell.date)}
                        isToday={isSameDay(cell.date, today)}
                        circle={circle}
                        inRange={
                          !!displayRange &&
                          isWithinRange(cell.date, displayRange.start, displayRange.end)
                        }
                        isFocused={!!tabbableDate && isSameDay(cell.date, tabbableDate)}
                        reduced={reduced}
                        onSelect={() => handleDayClick(cell.date)}
                        onHoverStart={() => {
                          if (canHover) setHoverDate(cell.date);
                        }}
                        onHoverEnd={() => {
                          if (canHover) setHoverDate(null);
                        }}
                        onFocusDate={() => setFocusedDate(cell.date)}
                        onFocusable={(el) => {
                          if (el) dayRefs.current.set(cell.date.getTime(), el);
                          else dayRefs.current.delete(cell.date.getTime());
                        }}
                        onKeyDown={(event) => handleDayKeyDown(cell.date, event)}
                      />
                    );
                  })}
                </div>
              ))}
            </div>
          </div>
        </LayoutGroup>
      </div>
      <div className="flex items-center justify-between gap-2 px-2 pb-1 pt-2">
        <div className="flex flex-wrap gap-1">
          {presets.map((preset) => {
            const active = isPresetActive(preset);
            return (
              <motion.button
                key={preset.id}
                type="button"
                aria-pressed={active}
                whileTap={reduced ? undefined : { scale: PRESS }}
                transition={spring.press}
                onClick={() => applyPreset(preset)}
                className={cn(
                  'h-compact cursor-pointer whitespace-nowrap rounded-full border px-3 text-small font-medium transition-colors',
                  FOCUS_RING,
                  active
                    ? 'border-info-line bg-info-tint text-info-strong'
                    : 'border-line bg-surface-card text-content-secondary hover:bg-surface',
                )}
              >
                {preset.label}
              </motion.button>
            );
          })}
        </div>
        <button
          type="button"
          onClick={() => {
            reset();
            onClear?.();
          }}
          className={cn(
            'shrink-0 cursor-pointer rounded-sm text-small font-medium text-content-muted underline-offset-2 transition-colors hover:text-content-primary hover:underline',
            FOCUS_RING,
          )}
        >
          {clearLabel}
        </button>
      </div>
      <div aria-live="polite" className="sr-only">
        {liveMessage}
      </div>
    </div>
  );
}

/** One week's slice of the range band. It grows out of the side facing `enterAnchor`
 *  when it appears and retracts toward `exitAnchor` when it goes, so a whole range reads
 *  as one ribbon being drawn and wound back. An end that carries a circle stops at that
 *  day's centre, tucked under the circle; the hovered end of a preview reaches
 *  `HOVER_REACH` into the day, under its hover circle, so there is no gap. */
function RowFill({
  week,
  range,
  circleStart,
  circleEnd,
  hovered,
  enterAnchor,
  exitAnchor,
  reduced,
}: {
  week: MonthGridDay[];
  range: DateRange | null;
  circleStart: boolean;
  circleEnd: boolean;
  hovered: Date | null;
  enterAnchor: number;
  exitAnchor: number;
  reduced: boolean;
}) {
  const cols = range
    ? week.flatMap((cell, col) =>
        cell.inMonth && isWithinRange(cell.date, range.start, range.end) ? [col] : [],
      )
    : [];
  const first = cols[0];
  const last = cols.at(-1);
  const fill = (() => {
    if (!range || first === undefined || last === undefined) return null;
    const firstDate = week[first]?.date ?? range.start;
    const lastDate = week[last]?.date ?? range.end;
    const isHovered = (date: Date) => !!hovered && isSameDay(date, hovered);
    // An end tucked under a selected circle stays square: a rounded cap there curves away
    // from the circle and leaves a notch, where a straight edge runs flush into it.
    const underStart = circleStart && isSameDay(firstDate, range.start) && !isHovered(firstDate);
    const underEnd = circleEnd && isSameDay(lastDate, range.end) && !isHovered(lastDate);
    const roundLeft =
      !underStart &&
      (first === 0 || week[first - 1]?.inMonth === false || isSameDay(firstDate, range.start));
    const roundRight =
      !underEnd &&
      (last === 6 || week[last + 1]?.inMonth === false || isSameDay(lastDate, range.end));
    const from =
      first +
      (isHovered(firstDate)
        ? 1 - HOVER_REACH
        : circleStart && isSameDay(firstDate, range.start)
          ? 0.5
          : 0);
    const to =
      last +
      1 -
      (isHovered(lastDate)
        ? 1 - HOVER_REACH
        : circleEnd && isSameDay(lastDate, range.end)
          ? 0.5
          : 0);
    if (to <= from) return null;
    return {
      left: (from / 7) * 100,
      width: ((to - from) / 7) * 100,
      firstTime: firstDate.getTime(),
      lastTime: lastDate.getTime(),
      leftRadius: roundLeft ? FILL_RADIUS : 0,
      rightRadius: roundRight ? FILL_RADIUS : 0,
    };
  })();

  // Both edges are placed as insets and eased with a short tween, never a spring: the
  // band follows the pointer across many days a second, and anything with momentum
  // overshoots the row or trails behind the cursor.
  const band = reduced ? INSTANT : tween.follow;
  const collapsed = (anchor: number) => {
    if (!fill) return {};
    let edge: number;
    if (anchor <= fill.firstTime) edge = fill.left;
    else if (anchor >= fill.lastTime) edge = fill.left + fill.width;
    else {
      const col = week.findIndex((c) => c.date.getTime() >= anchor);
      edge = ((Math.max(col, 0) + 0.5) / 7) * 100;
    }
    return {
      left: `${String(edge)}%`,
      right: `${String(100 - edge)}%`,
      opacity: 0,
      transition: band,
    };
  };

  return (
    <div aria-hidden="true" className="pointer-events-none absolute inset-0 overflow-hidden">
      <AnimatePresence initial={false} custom={exitAnchor}>
        {fill && (
          <motion.div
            key="fill"
            custom={enterAnchor}
            variants={{
              collapsed,
              shown: {
                left: `${String(fill.left)}%`,
                right: `${String(100 - fill.left - fill.width)}%`,
                opacity: 1,
                borderTopLeftRadius: fill.leftRadius,
                borderBottomLeftRadius: fill.leftRadius,
                borderTopRightRadius: fill.rightRadius,
                borderBottomRightRadius: fill.rightRadius,
                transition: band,
              },
            }}
            initial="collapsed"
            animate="shown"
            exit="collapsed"
            className="absolute inset-y-0 bg-info-tint"
          />
        )}
      </AnimatePresence>
    </div>
  );
}

function DayCell({
  cell,
  disabled,
  isToday,
  circle,
  inRange,
  isFocused,
  reduced,
  onSelect,
  onHoverStart,
  onHoverEnd,
  onFocusDate,
  onFocusable,
  onKeyDown,
}: {
  cell: MonthGridDay;
  disabled: boolean;
  isToday: boolean;
  circle: 'start' | 'end' | null;
  inRange: boolean;
  isFocused: boolean;
  reduced: boolean;
  onSelect: () => void;
  onHoverStart: () => void;
  onHoverEnd: () => void;
  onFocusDate: () => void;
  onFocusable: (el: HTMLButtonElement | null) => void;
  onKeyDown: (event: KeyboardEvent) => void;
}) {
  if (!cell.inMonth) return <div role="gridcell" aria-hidden="true" />;
  return (
    <div
      role="gridcell"
      aria-disabled={disabled}
      aria-selected={inRange}
      className="relative grid place-items-center"
      onPointerEnter={disabled ? undefined : onHoverStart}
      onPointerLeave={disabled ? undefined : onHoverEnd}
    >
      <motion.button
        ref={onFocusable}
        type="button"
        aria-label={formatFullDate(cell.date)}
        aria-current={isToday ? 'date' : undefined}
        whileTap={disabled || reduced ? undefined : { scale: PRESS }}
        transition={spring.press}
        tabIndex={isFocused ? 0 : -1}
        disabled={disabled}
        onClick={onSelect}
        onFocus={onFocusDate}
        onKeyDown={onKeyDown}
        className={cn(
          'relative grid size-tile cursor-pointer place-items-center rounded-full border border-transparent text-body tabular-nums transition-colors',
          FOCUS_RING,
          disabled
            ? 'cursor-not-allowed text-content-subtle opacity-50'
            : circle
              ? 'text-content-primary'
              : inRange
                ? 'text-content-primary hover:border-info-line hover:bg-surface-card'
                : 'text-content-secondary hover:border-line hover:bg-canvas',
        )}
      >
        <span className="relative">{cell.date.getDate()}</span>
        <AnimatePresence initial={false}>
          {circle && (
            <motion.span
              // Keyed by role: the same day can turn from the start circle into the end
              // circle, and reusing the element would leave it in the start circle's glide.
              key={circle}
              aria-hidden="true"
              layoutId={circle}
              initial={{ scale: 0.97, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.97, opacity: 0 }}
              transition={reduced ? INSTANT : spring.layout}
              className="absolute inset-0 grid place-items-center rounded-full bg-action-primary font-medium text-on-fill shadow-pill"
            >
              {/* The white numeral rides inside the circle, so the ink and the fill it is
                  read on are one element: it can never land on the white card mid-glide.
                  The plain numeral before it stays where it is and the circle covers it. */}
              {cell.date.getDate()}
            </motion.span>
          )}
        </AnimatePresence>
      </motion.button>
      {isToday && !circle && (
        <span
          aria-hidden="true"
          className="pointer-events-none absolute bottom-1 size-tick rounded-full bg-content-subtle"
        />
      )}
    </div>
  );
}
