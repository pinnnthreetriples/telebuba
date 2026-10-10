// One day, picked on the same calendar as `DateRangePicker`: the same card, month header,
// Monday-first grid, 34px day circles, action-blue selection and today's tick. The
// selected circle glides between days on the layout spring, as the range picker's do.
// Unlike the range picker it is not Russian-only: month and weekday names come from
// `Intl` for `locale`, because it sits inside screens that follow the app's language.
import { AnimatePresence, LayoutGroup, motion, useReducedMotion } from 'motion/react';
import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import type { KeyboardEvent, ReactNode } from 'react';

import { FOCUS_RING, pressScale, spring } from '@/shared/design-system';
import { cn } from '@/shared/lib/cn';
import {
  addDays,
  addMonths,
  firstEnabledDayInMonth,
  getMonthGrid,
  isSameDay,
  startOfDay,
  startOfMonth,
} from '@/shared/lib/dateRange';

import { Icon } from './Icon';

const PRESS = Number(pressScale.press);
const INSTANT = { duration: 0 } as const;

const capitalize = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);

export interface DatePickerProps {
  value: Date | null;
  onChange: (date: Date) => void;
  minDate?: Date;
  maxDate?: Date;
  /** The day marked as today (default: now), so a test or a static page can pin it. */
  today?: Date;
  /** BCP 47 tag for month and weekday names. */
  locale?: string;
  prevLabel?: string;
  nextLabel?: string;
  /** Move focus to the selected (or first pickable) day on mount — for a popover. */
  autoFocus?: boolean;
  /** A row under the calendar, on the outer card — the range picker's presets row. */
  footer?: ReactNode;
  className?: string;
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

/**
 * A month calendar for a single day. Arrow keys, Home/End and PageUp/PageDown move
 * through the days, skipping disabled ones; Enter or Space picks.
 */
export function DatePicker({
  value,
  onChange,
  minDate,
  maxDate,
  today: todayProp,
  locale = 'ru-RU',
  prevLabel = 'Предыдущий месяц',
  nextLabel = 'Следующий месяц',
  autoFocus = false,
  footer,
  className,
}: DatePickerProps) {
  const today = useMemo(() => startOfDay(todayProp ?? new Date()), [todayProp]);
  const [month, setMonth] = useState(() => startOfMonth(value ?? today));
  const [focusedDate, setFocusedDate] = useState<Date>(() => value ?? today);
  const reduced = useReducedMotion() ?? false;
  // Per instance: a shared layoutId would make two pickers on one page trade circles.
  const instanceId = useId();
  const gridRef = useRef<HTMLDivElement>(null);
  const focusFromKeyboard = useRef(autoFocus);
  useEffect(() => {
    if (!focusFromKeyboard.current) return;
    focusFromKeyboard.current = false;
    // The roving tab stop is the day to land on: the focused day, or the first pickable
    // one when the focused day is out of range.
    gridRef.current?.querySelector<HTMLButtonElement>('button[tabindex="0"]')?.focus();
  }, [focusedDate]);

  const isDisabled = useCallback(
    (date: Date) =>
      // By day: a focused or selected date can carry a time of day.
      (minDate !== undefined && startOfDay(date) < startOfDay(minDate)) ||
      (maxDate !== undefined && startOfDay(date) > startOfDay(maxDate)),
    [minDate, maxDate],
  );

  const weekdays = useMemo(
    () =>
      // Monday first. 1 January 2024 was a Monday.
      Array.from({ length: 7 }, (_, i) => {
        const date = new Date(2024, 0, 1 + i);
        return {
          short: capitalize(date.toLocaleDateString(locale, { weekday: 'short' })),
          full: date.toLocaleDateString(locale, { weekday: 'long' }),
        };
      }),
    [locale],
  );
  const weeks = useMemo(() => getMonthGrid(month), [month]);
  const tabbableDate = useMemo(() => {
    const visible =
      focusedDate.getMonth() === month.getMonth() &&
      focusedDate.getFullYear() === month.getFullYear();
    if (visible && !isDisabled(focusedDate)) return focusedDate;
    return firstEnabledDayInMonth(month, isDisabled);
  }, [focusedDate, month, isDisabled]);

  const monthTime = month.getTime();
  const atMinMonth = minDate !== undefined && monthTime <= startOfMonth(minDate).getTime();
  const atMaxMonth = maxDate !== undefined && monthTime >= startOfMonth(maxDate).getTime();

  const moveFocus = (from: Date, days: number) => {
    const direction = days < 0 ? -1 : 1;
    let next = addDays(from, days);
    for (let attempts = 0; isDisabled(next) && attempts < 42; attempts++) {
      next = addDays(next, direction);
    }
    if (isDisabled(next)) return;
    setMonth(startOfMonth(next));
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

  const monthName = capitalize(month.toLocaleDateString(locale, { month: 'long' }));

  return (
    <div className={cn('flex flex-col rounded-lg bg-canvas p-2 shadow-pop', className)}>
      <div className="rounded-md bg-surface-card p-4 shadow-seg">
        <div className="flex items-center justify-between gap-3">
          <div className="text-h3 font-medium text-content-primary">
            {monthName} <span className="text-content-subtle">{month.getFullYear()}</span>
          </div>
          <div className="flex items-center">
            <NavButton
              label={prevLabel}
              reduced={reduced}
              disabled={atMinMonth}
              onClick={() => setMonth((m) => addMonths(m, -1))}
            >
              <Icon name="chevron-left" size={16} />
            </NavButton>
            <NavButton
              label={nextLabel}
              reduced={reduced}
              disabled={atMaxMonth}
              onClick={() => setMonth((m) => addMonths(m, 1))}
            >
              <Icon name="chevron-right" size={16} />
            </NavButton>
          </div>
        </div>
        <LayoutGroup id={instanceId}>
          <div
            ref={gridRef}
            role="grid"
            aria-label={`${monthName} ${String(month.getFullYear())}`}
            className="mt-3 select-none"
          >
            <div role="row" className="grid grid-cols-7 gap-1 text-center type-small">
              {weekdays.map((day) => (
                <div key={day.full} role="columnheader" aria-label={day.full}>
                  {day.short}
                </div>
              ))}
            </div>
            <div className="mt-2 flex flex-col gap-1">
              {weeks.map((week) => (
                <div role="row" key={week[0]?.date.getTime()} className="grid grid-cols-7 gap-1">
                  {week.map((cell) => {
                    if (!cell.inMonth) {
                      return (
                        <div
                          key={cell.date.getTime()}
                          role="gridcell"
                          aria-hidden="true"
                          className="size-tile"
                        />
                      );
                    }
                    const disabled = isDisabled(cell.date);
                    const selected = value !== null && isSameDay(cell.date, value);
                    const isToday = isSameDay(cell.date, today);
                    const tabbable = tabbableDate !== null && isSameDay(cell.date, tabbableDate);
                    const day = cell.date.getDate();
                    return (
                      <div
                        key={cell.date.getTime()}
                        role="gridcell"
                        aria-selected={selected}
                        aria-disabled={disabled}
                        className="relative grid place-items-center"
                      >
                        <motion.button
                          type="button"
                          aria-label={cell.date.toLocaleDateString(locale, {
                            day: 'numeric',
                            month: 'long',
                            year: 'numeric',
                          })}
                          aria-current={isToday ? 'date' : undefined}
                          whileTap={disabled || reduced ? undefined : { scale: PRESS }}
                          transition={spring.press}
                          tabIndex={tabbable ? 0 : -1}
                          disabled={disabled}
                          onClick={() => onChange(cell.date)}
                          onFocus={() => setFocusedDate(cell.date)}
                          onKeyDown={(event) => handleDayKeyDown(cell.date, event)}
                          className={cn(
                            'relative grid size-tile cursor-pointer place-items-center rounded-full border border-transparent text-body tabular-nums transition-colors',
                            FOCUS_RING,
                            disabled
                              ? 'cursor-not-allowed text-content-subtle opacity-50'
                              : selected
                                ? 'text-content-primary'
                                : 'text-content-secondary hover:border-line hover:bg-canvas',
                          )}
                        >
                          <span className="relative">{day}</span>
                          <AnimatePresence initial={false}>
                            {selected && (
                              <motion.span
                                aria-hidden="true"
                                layoutId="selected"
                                initial={{ scale: 0.97, opacity: 0 }}
                                animate={{ scale: 1, opacity: 1 }}
                                exit={{ scale: 0.97, opacity: 0 }}
                                transition={reduced ? INSTANT : spring.layout}
                                className="absolute inset-0 grid place-items-center rounded-full bg-action-primary font-medium text-on-fill shadow-pill"
                              >
                                {day}
                              </motion.span>
                            )}
                          </AnimatePresence>
                        </motion.button>
                        {isToday && !selected && (
                          <span
                            aria-hidden="true"
                            className="pointer-events-none absolute bottom-1 size-tick rounded-full bg-content-subtle"
                          />
                        )}
                      </div>
                    );
                  })}
                </div>
              ))}
            </div>
          </div>
        </LayoutGroup>
      </div>
      {/* The calendar sets the card's width; a footer wraps inside it, never widens it. */}
      {footer !== undefined && <div className="px-1 pb-1 pt-2 contain-inline-size">{footer}</div>}
    </div>
  );
}
