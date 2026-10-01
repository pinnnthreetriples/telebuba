import { useId } from 'react';
import { useTranslation } from 'react-i18next';

import { formatLocalDateTime, formatRelativeTo } from '@/shared/lib';
import { Input } from '@/shared/ui';

import {
  fromLocalInput,
  MAX_LEAD_MS,
  MIN_LEAD_MS,
  runAtProblem,
  toLocalInput,
} from '../model/runAt';

// The publish-time field. The native picker renders in the BROWSER's locale, which
// need not be the app's language, so the line under it repeats the moment in the
// app's own words ("пт, 3 окт., 14:30 · через 2 ч") — the unambiguous reading, and
// the place a too-soon / too-far time is explained.
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
    <div className="flex min-w-0 flex-col gap-tight">
      <label className="flex min-w-0 flex-col gap-tight">
        <span className="type-label">{label}</span>
        <Input
          type="datetime-local"
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
      <span
        id={echoId}
        className={`type-caption ${problem !== null && value !== null ? 'text-danger-deep' : ''}`}
      >
        {echo}
      </span>
    </div>
  );
}
