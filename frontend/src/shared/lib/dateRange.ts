// Calendar-day arithmetic for a date range, in local time. Every function here works on
// days, not instants: the time of day is dropped on the way in, so a range picked at
// 23:59 and one picked at 00:01 are the same range.
//
// The helpers are the ones Devigner UI's date-range picker relies on
// (https://ui.devigner.cc/components/date-range-picker), reimplemented here so
// `DateRangePicker` does not depend on that package.

export interface DateRange {
  start: Date;
  end: Date;
}

export interface MonthGridDay {
  date: Date;
  /** False for the leading and trailing days that only pad the first and last week. */
  inMonth: boolean;
}

export function startOfDay(date: Date): Date {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

export function startOfMonth(date: Date): Date {
  return new Date(date.getFullYear(), date.getMonth(), 1);
}

export function isSameDay(a: Date, b: Date): boolean {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  );
}

/** -1, 0 or 1, by calendar day. */
export function compareDays(a: Date, b: Date): number {
  const diff = startOfDay(a).getTime() - startOfDay(b).getTime();
  return Math.sign(diff);
}

export function addDays(date: Date, days: number): Date {
  const next = startOfDay(date);
  next.setDate(next.getDate() + days);
  return next;
}

/** The first of the month `months` away. */
export function addMonths(date: Date, months: number): Date {
  return new Date(date.getFullYear(), date.getMonth() + months, 1);
}

export function isWithinRange(date: Date, start: Date, end: Date): boolean {
  return compareDays(date, start) >= 0 && compareDays(date, end) <= 0;
}

/** Nights between two days. Rounded, so a daylight-saving shift is not a fraction. */
export function nightsBetween(start: Date, end: Date): number {
  return Math.round((startOfDay(end).getTime() - startOfDay(start).getTime()) / 86_400_000);
}

/** The farthest day from `start` toward `end` reachable without crossing a disabled day.
 *  A range cannot span a day that cannot be booked, so it stops short of it. */
export function clampRangeEnd(start: Date, end: Date, isDisabled?: (date: Date) => boolean): Date {
  if (!isDisabled) return startOfDay(end);
  const step = compareDays(end, start) >= 0 ? 1 : -1;
  let reached = startOfDay(start);
  let next = addDays(start, step);
  while (
    (step > 0 ? compareDays(next, end) <= 0 : compareDays(next, end) >= 0) &&
    !isDisabled(next)
  ) {
    reached = next;
    next = addDays(next, step);
  }
  return reached;
}

/** The month as whole weeks, Monday first, padded with the neighbouring months' days. */
export function getMonthGrid(month: Date): MonthGridDay[][] {
  const first = startOfMonth(month);
  const last = new Date(first.getFullYear(), first.getMonth() + 1, 0);
  const lead = (first.getDay() + 6) % 7;
  const trail = 6 - ((last.getDay() + 6) % 7);
  const origin = addDays(first, -lead);
  const days = Array.from({ length: lead + last.getDate() + trail }, (_, i) => {
    const date = addDays(origin, i);
    return { date, inMonth: date.getMonth() === first.getMonth() };
  });
  const weeks: MonthGridDay[][] = [];
  for (let i = 0; i < days.length; i += 7) weeks.push(days.slice(i, i + 7));
  return weeks;
}

/** First day of the month containing `anchor` that is not disabled, or null. */
export function firstEnabledDayInMonth(
  anchor: Date,
  isDisabled: (date: Date) => boolean,
): Date | null {
  const first = startOfMonth(anchor);
  const length = new Date(first.getFullYear(), first.getMonth() + 1, 0).getDate();
  for (let i = 0; i < length; i++) {
    const candidate = addDays(first, i);
    if (!isDisabled(candidate)) return candidate;
  }
  return null;
}
