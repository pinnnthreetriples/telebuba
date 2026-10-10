import { useEffect, useId, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { fieldBase } from '@/shared/design-system';
import { formatLocalDateTime, formatRelativeTo } from '@/shared/lib';
import { cn } from '@/shared/lib/cn';
import { DatePicker, Icon, Input } from '@/shared/ui';

import {
  defaultRunAt,
  fromLocalInput,
  MAX_LEAD_MS,
  MIN_LEAD_MS,
  runAtProblem,
  toLocalInput,
} from '../model/runAt';

// The publish-time field. The typed field renders in the BROWSER's locale, which
// need not be the app's language, so the line under it repeats the moment in the
// app's own words ("пт, 3 окт., 14:30 · через 2 ч") — the unambiguous reading, and
// the place a too-soon / too-far time is explained. The day is picked on the design
// system's own calendar (`DatePicker`) in a popover instead of the browser's picker,
// which is painted by the OS and ignores the app's language and colours; the time of
// day stays typed in the field.

// The calendar's trigger wears the field's own shell — height, border, radius, focus —
// so it reads as the second half of the same control, squared off to fit one glyph.
const CALENDAR_TRIGGER =
  'grid aspect-square w-auto shrink-0 cursor-pointer place-items-center p-0 border-line text-content-muted hover:border-line-strong hover:text-content-primary disabled:cursor-default disabled:bg-surface disabled:text-content-subtle';

/** `day` at the time of day `ms` carries. */
function onDay(day: Date, ms: number): number {
  const time = new Date(ms);
  return new Date(
    day.getFullYear(),
    day.getMonth(),
    day.getDate(),
    time.getHours(),
    time.getMinutes(),
  ).getTime();
}

export function ScheduleTimeField({
  value,
  onChange,
  now,
  label,
  minLeadMs = MIN_LEAD_MS,
  size = 'md',
  disabled = false,
  readOnly = false,
  autoFocus = false,
}: {
  value: number | null;
  onChange: (ms: number | null) => void;
  now: number;
  label: string;
  minLeadMs?: number;
  size?: 'md' | 'sm';
  disabled?: boolean;
  // Locked but still focusable: while a save is in flight, a disabled field would
  // drop focus to the page and hand Escape to the dialog around the editor.
  readOnly?: boolean;
  // For a field that appears on demand (an inline editor): focus goes to it, not
  // to the page, so Escape and Enter reach the editor around it.
  autoFocus?: boolean;
}) {
  const { t, i18n } = useTranslation();
  const echoId = useId();
  const popoverId = useId();
  const [open, setOpen] = useState(false);
  // Below the field unless the viewport has no room for the calendar there.
  const [above, setAbove] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const locked = disabled || readOnly;

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
  useEffect(() => {
    if (locked) setOpen(false);
  }, [locked]);

  const toggle = () => {
    if (open) {
      setOpen(false);
      return;
    }
    const rect = rootRef.current?.getBoundingClientRect();
    // The calendar is about 360px tall; flip only when it fits above and not below.
    setAbove(!!rect && window.innerHeight - rect.bottom < 380 && rect.top > 380);
    setOpen(true);
  };
  const pick = (day: Date) => {
    const earliest = now + minLeadMs;
    const ms = onDay(day, value ?? defaultRunAt(now));
    // A day picked today at a time already gone moves to the first tidy slot ahead.
    onChange(ms < earliest ? Math.max(onDay(day, defaultRunAt(now)), earliest) : ms);
    setOpen(false);
    buttonRef.current?.focus();
  };

  const problem = runAtProblem(value, now, minLeadMs);
  const echo =
    value === null || problem !== null
      ? t(`accounts.schedule.problem.${problem ?? 'empty'}`, {
          n: Math.ceil(minLeadMs / 60_000),
        })
      : t('accounts.schedule.echo', {
          when: formatLocalDateTime(value, i18n.language),
          rel: formatRelativeTo(value, now, i18n.language),
        });
  return (
    // The echo sits beside the label, not inside it: inside, it would become part of
    // the field's accessible name instead of its description.
    <div className="flex min-w-0 flex-col gap-2">
      <div
        ref={rootRef}
        className="relative flex min-w-0 items-end gap-2"
        onKeyDown={(event) => {
          // Escape closes the calendar and stops there: the editor and the dialog
          // around this field have their own Escape.
          if (event.key !== 'Escape' || !open) return;
          event.stopPropagation();
          setOpen(false);
          buttonRef.current?.focus();
        }}
        onBlur={(event) => {
          // Tabbing out of the calendar closes it rather than leaving it open behind focus.
          if (open && !event.currentTarget.contains(event.relatedTarget)) setOpen(false);
        }}
      >
        <label className="flex min-w-0 flex-1 flex-col gap-2">
          <span className="type-body-medium text-content-secondary">{label}</span>
          <Input
            type="datetime-local"
            className="tb-nopicker"
            size={size}
            step={60}
            value={value === null ? '' : toLocalInput(value)}
            min={toLocalInput(now + minLeadMs)}
            max={toLocalInput(now + MAX_LEAD_MS)}
            invalid={value !== null && problem !== null}
            disabled={disabled}
            readOnly={readOnly}
            autoFocus={autoFocus}
            aria-describedby={echoId}
            onChange={(event) => {
              onChange(fromLocalInput(event.target.value));
            }}
          />
        </label>
        <button
          ref={buttonRef}
          type="button"
          aria-label={t('accounts.schedule.pickDay')}
          aria-haspopup="dialog"
          aria-expanded={open}
          aria-controls={open ? popoverId : undefined}
          disabled={locked}
          onClick={toggle}
          className={cn(fieldBase({ size }), CALENDAR_TRIGGER, open && 'border-action-primary')}
        >
          <Icon name="calendar" size={16} />
        </button>
        {open && (
          <div
            id={popoverId}
            role="dialog"
            aria-label={t('accounts.schedule.pickDay')}
            className={`absolute right-0 z-pop ${above ? 'bottom-full mb-2' : 'top-full mt-2'}`}
          >
            <DatePicker
              value={value === null ? null : new Date(value)}
              onChange={pick}
              minDate={new Date(now + minLeadMs)}
              maxDate={new Date(now + MAX_LEAD_MS)}
              today={new Date(now)}
              locale={i18n.language}
              prevLabel={t('accounts.schedule.prevMonth')}
              nextLabel={t('accounts.schedule.nextMonth')}
              autoFocus
            />
          </div>
        )}
      </div>
      <span
        id={echoId}
        aria-live="polite"
        className={`type-small ${problem !== null && value !== null ? 'text-danger-deep' : ''}`}
      >
        {echo}
      </span>
    </div>
  );
}
