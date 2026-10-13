import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Button, Input, Switch } from '@/shared/ui';

import type { FullSchedule } from '../model/schedule';

const DAYS = [0, 1, 2, 3, 4, 5, 6] as const;

function NumberField({
  label,
  value,
  min,
  max,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  onChange: (value: number) => void;
}) {
  // The typed text, so a field can be cleared and retyped; only an in-range whole
  // number reaches the schedule, and leaving the field shows the value in force.
  const [text, setText] = useState(String(value));
  // Follow a value changed from outside (the other break field moving this one), but
  // keep text being typed that already means the same number.
  const [shown, setShown] = useState(value);
  if (value !== shown) {
    setShown(value);
    if (Number(text) !== value) setText(String(value));
  }
  return (
    <Input
      type="number"
      size="sm"
      aria-label={label}
      min={min}
      max={max}
      step={1}
      value={text}
      onChange={(event) => {
        setText(event.target.value);
        const next = Number(event.target.value);
        if (event.target.value !== '' && Number.isInteger(next) && next >= min && next <= max) {
          onChange(next);
        }
      }}
      onBlur={() => {
        setText(String(value));
      }}
      className="w-number"
    />
  );
}

// "When to send": the working window and days, the client's own clock, series
// breaks, a scheduled start and how many accounts send at once.
export function ScheduleSection({
  schedule,
  onChange,
  startAt,
  onStartAtChange,
}: {
  schedule: FullSchedule;
  onChange: (schedule: FullSchedule) => void;
  // A `datetime-local` value; empty starts the run at once.
  startAt: string;
  onStartAtChange: (value: string) => void;
}) {
  const { t } = useTranslation();
  const set = (patch: Partial<FullSchedule>) => {
    onChange({ ...schedule, ...patch });
  };
  const breakMinutes = (seconds: number) => Math.round(seconds / 60);

  return (
    <section className="space-y-3">
      <h4 className="type-body-medium text-content-secondary">
        {t('accounts.messages.schedule.title')}
      </h4>
      <div className="flex flex-wrap items-center gap-2 type-small">
        <span>{t('accounts.messages.schedule.hours')}</span>
        <Input
          type="time"
          size="sm"
          aria-label={t('accounts.messages.schedule.from')}
          value={schedule.work_start}
          onChange={(event) => {
            set({ work_start: event.target.value });
          }}
          className="w-stamp"
        />
        <span>–</span>
        <Input
          type="time"
          size="sm"
          aria-label={t('accounts.messages.schedule.to')}
          value={schedule.work_end}
          onChange={(event) => {
            set({ work_end: event.target.value });
          }}
          className="w-stamp"
        />
        <span className="text-content-muted">{schedule.operator_timezone}</span>
      </div>
      <div
        role="group"
        aria-label={t('accounts.messages.schedule.days')}
        className="flex flex-wrap gap-1"
      >
        {DAYS.map((day) => {
          const on = schedule.work_days.includes(day);
          return (
            <Button
              key={day}
              size="sm"
              variant={on ? 'primary' : 'secondary'}
              aria-pressed={on}
              onClick={() => {
                const next = on
                  ? schedule.work_days.filter((value) => value !== day)
                  : [...schedule.work_days, day].sort();
                if (next.length > 0) set({ work_days: next });
              }}
            >
              {t(`accounts.messages.schedule.day.${String(day)}`)}
            </Button>
          );
        })}
      </div>
      <label className="flex items-center justify-between gap-3 type-small">
        <span>{t('accounts.messages.schedule.clientTime')}</span>
        <Switch
          checked={schedule.client_timezone}
          onChange={(value) => {
            set({ client_timezone: value });
          }}
          label={t('accounts.messages.schedule.clientTime')}
        />
      </label>
      <div className="flex flex-wrap items-center gap-2 type-small">
        <span>{t('accounts.messages.schedule.breakAfter')}</span>
        <NumberField
          label={t('accounts.messages.schedule.seriesSize')}
          value={schedule.series_size}
          min={0}
          max={100}
          onChange={(value) => {
            set({ series_size: value });
          }}
        />
        <span>{t('accounts.messages.schedule.breakFor')}</span>
        <NumberField
          label={t('accounts.messages.schedule.breakMin')}
          value={breakMinutes(schedule.series_break_min_seconds)}
          min={0}
          max={120}
          onChange={(value) => {
            set({
              series_break_min_seconds: value * 60,
              series_break_max_seconds: Math.max(value * 60, schedule.series_break_max_seconds),
            });
          }}
        />
        <span>–</span>
        <NumberField
          label={t('accounts.messages.schedule.breakMax')}
          value={breakMinutes(schedule.series_break_max_seconds)}
          min={0}
          max={120}
          onChange={(value) => {
            set({
              series_break_max_seconds: value * 60,
              series_break_min_seconds: Math.min(value * 60, schedule.series_break_min_seconds),
            });
          }}
        />
        <span>{t('accounts.messages.schedule.minutes')}</span>
      </div>
      <div className="flex flex-wrap items-center gap-2 type-small">
        <span>{t('accounts.messages.schedule.startAt')}</span>
        <Input
          type="datetime-local"
          size="sm"
          aria-label={t('accounts.messages.schedule.startAt')}
          value={startAt}
          onChange={(event) => {
            onStartAtChange(event.target.value);
          }}
          className="w-tip"
        />
      </div>
      <div className="flex flex-wrap items-center gap-2 type-small">
        <span>{t('accounts.messages.schedule.concurrency')}</span>
        <NumberField
          label={t('accounts.messages.schedule.concurrency')}
          value={schedule.concurrency}
          min={1}
          max={10}
          onChange={(value) => {
            set({ concurrency: value });
          }}
        />
      </div>
    </section>
  );
}
