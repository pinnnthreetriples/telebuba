import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { AccountLimitGauge, AccountLimitsView } from '@/shared/api';
import { Button, Modal, toastError } from '@/shared/ui';

import {
  accountLimitsQueryOptions,
  neurocommentBoardQueryOptions,
  updateAccountLimitsMutation,
} from '../api/campaign.queries';

// The three caps in the order they bind: an account has to get INTO a channel before it
// can comment there, and the per-hour ceiling is spent before the per-channel day one.
const KEYS = ['joins', 'comments_per_hour', 'comments_per_channel_per_day'] as const;
type LimitKey = (typeof KEYS)[number];

// The API field each gauge writes back through. Same three caps, different vocabulary:
// the view names what is measured, the update names what is stored.
const FIELD = {
  joins: 'max_joins_per_day',
  comments_per_hour: 'max_comments_per_hour',
  comments_per_channel_per_day: 'max_comments_per_channel_per_day',
} as const;

// The lowest value each cap accepts, mirroring schemas/neurocomment_limits.py. The hourly
// cap starts at 1 because its gate is a bare ">=" — a zero there would refuse every
// comment instead of lifting the cap, so the API rejects it, and offering it here meant a
// 422 that also discarded the operator's other two edits.
const MIN = { joins: 0, comments_per_hour: 1, comments_per_channel_per_day: 0 } as const;
// Sanity ceiling, same as the API's: past 64 bits sqlite raises and the write became a 500.
const CAP_MAX = 10_000;

// `null` = the field is untouched and keeps whatever the account already had; a number is
// an override; `''` is the operator having cleared the box, which saves as "follow the
// fleet". Three states, because "no override" and "zero" are different limits.
type Draft = Partial<Record<LimitKey, number | ''>>;

function share(gauge: AccountLimitGauge): number {
  return gauge.limit > 0 ? gauge.used / gauge.limit : 0;
}

function tone(gauge: AccountLimitGauge): 'full' | 'near' | 'ok' {
  const ratio = share(gauge);
  if (gauge.limit > 0 && ratio >= 1) return 'full';
  return ratio >= 0.8 ? 'near' : 'ok';
}

const BAR = { full: 'bg-danger', near: 'bg-warning', ok: 'bg-success' } as const;

// A rolling window frees one slot at a time, so the reset is a moment, not a countdown to
// midnight. Local time and to the minute: the operator compares it against a log line.
function resetLabel(at: string | null | undefined, locale: string): string | null {
  if (!at) return null;
  return new Date(at).toLocaleString(locale, {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function LimitRow({
  gauge,
  label,
  hint,
  min,
  draft,
  onDraft,
}: {
  gauge: AccountLimitGauge;
  label: string;
  hint: string;
  min: number;
  draft: number | '' | undefined;
  onDraft: (value: number | '') => void;
}) {
  const { t, i18n } = useTranslation();
  const state = tone(gauge);
  const width = gauge.limit > 0 ? Math.min(100, Math.round(share(gauge) * 100)) : 0;
  const resets = resetLabel(gauge.resets_at, i18n.language);
  const value = draft === undefined ? (gauge.overridden ? gauge.limit : '') : draft;

  return (
    <div className="border-b border-line-row py-[15px] last:border-b-0">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-lead font-semibold text-ink">{label}</span>
        <span
          className={`font-mono text-body font-semibold tabular-nums ${
            state === 'full' ? 'text-danger' : 'text-ink-muted'
          }`}
        >
          {gauge.used} / {gauge.limit > 0 ? gauge.limit : '∞'}
        </span>
      </div>
      <div className="mt-2 h-[6px] overflow-hidden rounded-[3px] bg-track">
        <div className={`h-full rounded-[3px] ${BAR[state]}`} style={{ width: `${width}%` }} />
      </div>
      <div className="mt-[9px] flex flex-wrap items-center justify-between gap-3">
        <span className="min-w-[11rem] flex-1 text-tiny text-ink-subtle">
          {hint}
          {resets ? ` · ${t('neurocomment.modal.limits.resetsAt', { at: resets })}` : ''}
        </span>
        <input
          type="number"
          min={min}
          max={CAP_MAX}
          step={1}
          value={value}
          aria-label={label}
          placeholder={String(gauge.fleet_default)}
          onChange={(e) => {
            // Clamped and truncated here rather than left to the API: every value this box
            // can produce must be one the API accepts, or a full-replace save throws away
            // the edits made in the other two rows along with this one.
            onDraft(
              e.target.value === ''
                ? ''
                : Math.min(CAP_MAX, Math.max(min, Math.trunc(Number(e.target.value)) || min)),
            );
          }}
          className="w-[74px] rounded-md border border-line-input bg-white px-[9px] py-[6px] text-right font-mono text-body font-semibold text-ink"
        />
      </div>
      <div className="mt-[6px] text-tiny text-ink-subtle">
        {value === ''
          ? t('neurocomment.modal.limits.fleetValue', { value: gauge.fleet_default })
          : t('neurocomment.modal.limits.ownValue', { value: gauge.fleet_default })}
      </div>
    </div>
  );
}

// Design modal: account-limits — the three rolling-window caps of one account, what each
// has spent, when it frees a slot, and the box that overrides it. Opened from the
// "Лимиты" chip in NeuroAccountsModal.
export function AccountLimitsModal({
  accountId,
  name,
  campaignId,
  onClose,
}: {
  accountId: string;
  name: string;
  // The board whose cards count against these caps, so a save refreshes them at once
  // instead of leaving the denominator behind until the next poll. Absent when the modal
  // is opened without a selected campaign.
  campaignId?: string | null;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const query = useQuery(accountLimitsQueryOptions({ path: { account_id: accountId } }));
  const save = useMutation(updateAccountLimitsMutation());
  const [draft, setDraft] = useState<Draft>({});
  const view: AccountLimitsView | undefined = query.data;

  const hints: Record<LimitKey, string> = {
    joins: t('neurocomment.modal.limits.window.day'),
    comments_per_hour: t('neurocomment.modal.limits.window.hour'),
    comments_per_channel_per_day: view?.busiest_channel
      ? t('neurocomment.modal.limits.window.channel', { channel: view.busiest_channel })
      : t('neurocomment.modal.limits.window.day'),
  };

  const submit = async () => {
    if (!view) return;
    // A full replace, every time: the API reads a null field as "drop this override", which
    // is the only way the operator can hand a cap back to the fleet.
    const body = Object.fromEntries(
      KEYS.map((key) => {
        const value = draft[key] ?? (view[key].overridden ? view[key].limit : '');
        // Clamped on the way out as well as on the way in: an untouched row echoes back a
        // STORED cap, and a stored value predating this ceiling would otherwise 422 the
        // whole replace — taking the rows the operator did edit down with it, with no way
        // out from the screen.
        return [FIELD[key], value === '' ? null : Math.min(CAP_MAX, Math.max(MIN[key], value))];
      }),
    );
    // mutateAsync, not per-call callbacks: this modal is rendered per account row and can
    // be closed mid-flight, which drops mutate()'s callbacks along with the component.
    try {
      await save.mutateAsync({ path: { account_id: accountId }, body });
    } catch {
      toastError(t('neurocomment.modal.limits.saveFailed'));
      return;
    }
    await queryClient.invalidateQueries({
      queryKey: accountLimitsQueryOptions({ path: { account_id: accountId } }).queryKey,
    });
    if (campaignId != null) {
      await queryClient.invalidateQueries({
        queryKey: neurocommentBoardQueryOptions({ path: { campaign_id: campaignId } }).queryKey,
      });
    }
    onClose();
  };

  return (
    <Modal
      onClose={onClose}
      className="w-[440px]"
      label={t('neurocomment.modal.limits.title', { name })}
    >
      <div className="flex items-center gap-md border-b border-line-row px-6 pb-[15px] pt-5">
        <span className="flex h-[34px] w-[34px] shrink-0 items-center justify-center rounded-lg bg-primary-tint text-primary-deep">
          <svg
            width="18"
            height="18"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
          >
            <path d="M12 20a8 8 0 1 0-8-8" />
            <path d="m12 12 4-3" />
            <path d="M4 12H2M4.9 6.3 3.5 4.9M12 4V2" />
          </svg>
        </span>
        <div>
          <div className="text-title font-bold text-ink">
            {t('neurocomment.modal.limits.title', { name })}
          </div>
          <div className="mt-[2px] text-body text-ink-subtle">
            {t('neurocomment.modal.limits.sub')}
          </div>
        </div>
      </div>

      <div className="px-6 pb-1 pt-1">
        {view ? (
          KEYS.map((key) => (
            <LimitRow
              key={key}
              gauge={view[key]}
              label={t(`neurocomment.modal.limits.cap.${key}`)}
              hint={hints[key]}
              min={MIN[key]}
              draft={draft[key]}
              onDraft={(value) => {
                setDraft((d) => ({ ...d, [key]: value }));
              }}
            />
          ))
        ) : (
          <div className="px-[10px] py-8 text-center text-lead text-ink-subtle">
            {query.isError
              ? t('neurocomment.modal.limits.loadFailed')
              : t('neurocomment.modal.limits.loading')}
          </div>
        )}
      </div>

      <div className="mx-6 mb-1 rounded-lg border border-line bg-surface px-3 py-[10px] text-tiny text-ink-muted">
        {t('neurocomment.modal.limits.sharedJoins')}
      </div>

      <div className="flex justify-between gap-3 border-t border-line-row px-6 pb-5 pt-[14px]">
        <button
          type="button"
          onClick={() => {
            setDraft(Object.fromEntries(KEYS.map((key) => [key, ''])));
          }}
          className="rounded-full border border-line-strong bg-white px-[18px] py-[9px] text-lead font-medium text-ink-muted"
        >
          {t('neurocomment.modal.limits.resetAll')}
        </button>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-full border border-line-strong bg-white px-[18px] py-[9px] text-lead font-medium text-ink-muted"
          >
            {t('neurocomment.modal.cancel')}
          </button>
          <Button
            variant="primary"
            onClick={() => {
              void submit();
            }}
            disabled={!view || save.isPending}
          >
            {t('neurocomment.modal.limits.save')}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
