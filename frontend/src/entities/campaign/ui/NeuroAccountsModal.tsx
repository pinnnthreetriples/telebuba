import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { AccountLimitGauge } from '@/shared/api';
import { Button, ConfirmModal, FeedbackMark, IconButton, Modal } from '@/shared/ui';

import { accountLimitsQueryOptions } from '../api/campaign.queries';
import { AccountLimitsModal } from './AccountLimitsModal';

export interface NeuroAccountRow {
  account_id: string;
  name: string;
  linked: boolean;
  pinned_channels: string[];
  // Channels this account is banned in for good (#30). The board's channel row cannot
  // show it — one banned account among five ready ones still reads "Готов" — and this
  // modal is where the only remedy lives: add another account to the campaign.
  banned_channels?: string[];
}

// Stable React key for the "all channels" row (an empty subset = serve all).
const ALL_CHANNELS = 'all';

// Channels are stored as the operator typed them, so a campaign's list is usually a
// column of "https://t.me/…" sharing the first 13 characters — the identical part is
// exactly what a truncating label keeps. Show what tells them apart.
function shortChannel(channel: string): string {
  return channel.replace(/^(?:https?:\/\/)?(?:www\.)?t\.me\//i, '');
}

function CheckIcon() {
  return (
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
  );
}

// The three caps in the order they bind — same order as the limits modal's rows.
const GAUGES = ['joins', 'comments_per_hour', 'comments_per_channel_per_day'] as const;

// Optional on purpose: this reads a payload off the network, and a chip that throws
// would take the whole accounts modal down with it over a cosmetic number.
function share(gauge: AccountLimitGauge | undefined): number {
  return gauge && gauge.limit > 0 ? Math.min(1, gauge.used / gauge.limit) : 0;
}

// The row's at-a-glance: three micro-bars (one per cap, height = how much is spent) and
// the number of the TIGHTEST one, which is the cap that will actually stop this account.
// A colour change is reserved for the two states worth interrupting a scan for — at the
// cap, and within a fifth of it.
// ponytail: one request per LINKED row. The unlinked rows are every graduated account not
// yet on this campaign, which is the whole warmed fleet — they get no chip, so opening the
// modal costs one read per account actually doing the work. If a campaign's roster ever
// grows past tens, add a bulk `/limits?account_ids=` read rather than a cache here.
function LimitsChip({ accountId, onOpen }: { accountId: string; onOpen: () => void }) {
  const { t } = useTranslation();
  const { data } = useQuery(accountLimitsQueryOptions({ path: { account_id: accountId } }));
  // An uncapped gauge (limit 0) scores below every capped one, so it can only win when
  // nothing is capped at all — otherwise a fresh account with joins set to "no cap" would
  // advertise `0/∞` while its hourly cap was the one actually binding.
  const rank = (key: (typeof GAUGES)[number]) => (data?.[key]?.limit ? share(data[key]) : -1);
  const tightest = data
    ? GAUGES.reduce((best, key) => (rank(key) > rank(best) ? key : best))
    : null;
  const worst = share(tightest ? data?.[tightest] : undefined);
  const binding = tightest ? data?.[tightest] : undefined;
  const skin =
    worst >= 1
      ? 'border-danger-line bg-danger-tint text-danger'
      : worst >= 0.8
        ? 'border-warning-line bg-warning-tint text-warning'
        : 'border-line-input bg-white text-ink-muted hover:border-line-strong';

  return (
    <button
      type="button"
      onClick={onOpen}
      // No aria-label: it would override the button's own text, and the spend IS the
      // label worth hearing. The title carries the word for a pointer user.
      title={t('neurocomment.modal.neuroAccounts.limits')}
      className={`flex shrink-0 items-center gap-sm rounded-md border px-[10px] py-[7px] text-body font-medium ${skin}`}
    >
      <span className="flex h-3 items-end gap-[2px]">
        {GAUGES.map((key) => {
          const spent = data ? share(data[key]) : 0;
          return (
            <i
              key={key}
              style={{ height: `${Math.max(3, Math.round(spent * 12))}px` }}
              className={`block w-[3px] rounded-[1px] ${spent > 0 ? 'bg-current' : 'bg-track'}`}
            />
          );
        })}
      </span>
      <span className="font-mono text-tiny font-semibold tabular-nums">
        {binding
          ? `${binding.used}/${binding.limit > 0 ? binding.limit : '∞'}`
          : t('neurocomment.modal.neuroAccounts.limits')}
      </span>
    </button>
  );
}

function AccountRow({
  account,
  channels,
  campaignId,
  onPick,
  onRemove,
  onChannelChange,
  result,
}: {
  account: NeuroAccountRow;
  channels: string[];
  campaignId?: string | null;
  onPick: (accountId: string) => void;
  onRemove: (accountId: string) => void;
  onChannelChange: (accountId: string, channels: string[]) => void;
  result?: 'ok' | 'err';
}) {
  const { t } = useTranslation();
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [limitsOpen, setLimitsOpen] = useState(false);
  const [open, setOpen] = useState(false);
  const banned = account.banned_channels ?? [];

  // A linked account targets a subset of the campaign's channels, or an empty
  // subset = all of them. Any already-selected channel no longer on the campaign
  // (since removed) is still surfaced so it can be un-checked.
  const selected = account.pinned_channels;
  const options = [...channels, ...selected.filter((c) => !channels.includes(c))];
  const allChannels = t('neurocomment.modal.neuroAccounts.allChannels');
  const triggerLabel =
    selected.length === 0
      ? allChannels
      : selected.length === 1
        ? shortChannel(selected[0]!)
        : t('neurocomment.modal.neuroAccounts.channelsSelected', { count: selected.length });

  // Multi-select: toggling a channel keeps the menu open; "Все каналы" clears the
  // whole subset (= all). The empty list is the "serve all channels" sentinel.
  const toggleChannel = (channel: string) => {
    onChannelChange(
      account.account_id,
      selected.includes(channel) ? selected.filter((c) => c !== channel) : [...selected, channel],
    );
  };

  return (
    <div className="border-b border-line-row py-[11px]">
      <div className="flex flex-wrap items-center gap-md">
        <FeedbackMark result={result} />
        <span className="min-w-0 flex-1 truncate text-lead font-semibold text-ink">
          {account.name}
        </span>
        {account.linked ? (
          <LimitsChip
            accountId={account.account_id}
            onOpen={() => {
              setLimitsOpen(true);
            }}
          />
        ) : null}
        {account.linked ? (
          // Each linked account gets a ~180px multi-select of the campaign's channels;
          // an empty selection ("Все каналы") = comment on all. Custom tb-dd list (not a
          // native <select>) so it matches the design and allows multi-pick.
          <button
            type="button"
            aria-haspopup="listbox"
            aria-expanded={open}
            aria-label={t('neurocomment.modal.neuroAccounts.channelLabel')}
            onClick={() => {
              setOpen((v) => !v);
            }}
            onKeyDown={(e) => {
              if (e.key === 'Escape') setOpen(false);
            }}
            // A single pinned channel is the one label that can still be truncated here,
            // so keep the full link reachable without opening the list.
            title={selected.length === 1 ? selected[0] : undefined}
            className="tb-time flex w-full shrink-0 items-center justify-between gap-sm rounded-lg border border-line-input bg-white px-[11px] py-[8px] text-body text-ink sm:w-[180px]"
          >
            <span className={`min-w-0 truncate ${selected.length ? '' : 'text-ink-subtle'}`}>
              {triggerLabel}
            </span>
            <span className={`tb-ddchev flex shrink-0 text-ink-subtle ${open ? 'open' : ''}`}>
              <svg
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
              >
                <path d="m6 9 6 6 6-6" />
              </svg>
            </span>
          </button>
        ) : (
          <button
            type="button"
            onClick={() => {
              onPick(account.account_id);
            }}
            className="w-full shrink-0 rounded-md border border-dashed border-line-strong bg-white px-[11px] py-[8px] text-body font-medium text-primary hover:border-primary sm:w-[180px]"
          >
            {t('neurocomment.modal.neuroAccounts.assign')}
          </button>
        )}
        <IconButton
          size="lg"
          tone="danger"
          aria-label={t('neurocomment.modal.neuroAccounts.remove')}
          onClick={() => {
            setConfirmRemove(true);
          }}
        >
          <svg
            width="15"
            height="15"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.9"
          >
            <path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" />
          </svg>
        </IconButton>
      </div>
      {banned.length > 0 ? (
        // A per-pair ban is permanent — no retry, no un-ban — so the line states the
        // fact and nothing else; the operator's move is the "Добавить в кампанию"
        // button already on this screen.
        <div className="mt-[6px] text-tiny text-danger">
          {t('neurocomment.modal.neuroAccounts.banned', {
            channels: banned.map(shortChannel).join(', '),
          })}
        </div>
      ) : null}
      {account.linked ? (
        // The list expands inside the row instead of floating over it: the modal is its
        // own scroll box, so an absolutely-positioned menu was clipped at the modal edge
        // and had to be scrolled into view. At row width the channels also fit.
        // The box styling is open-only on purpose: under border-box a collapsed
        // ``max-height: 0`` still reserves its border and padding, which in flow (unlike
        // the old absolute menu) would leave 10px of phantom gap in every linked row.
        <div
          role="listbox"
          aria-multiselectable
          // .tb-dd collapses visually only; without this every channel option of
          // every linked row kept its tab stop while closed. See the note in LogsPage.
          inert={!open}
          className={`tb-dd ${open ? 'open mt-2 rounded-lg border border-line bg-white p-1 shadow-pop' : ''}`}
        >
          <button
            key={ALL_CHANNELS}
            type="button"
            role="option"
            aria-selected={selected.length === 0}
            onClick={() => {
              onChannelChange(account.account_id, []);
            }}
            className={`flex w-full items-center justify-between gap-sm rounded-sm px-[10px] py-2 text-left text-body transition-colors hover:bg-primary-tint ${
              selected.length === 0 ? 'font-medium text-primary-deep' : 'text-ink'
            }`}
          >
            <span className="min-w-0 truncate">{allChannels}</span>
            {selected.length === 0 ? <CheckIcon /> : null}
          </button>
          {options.map((channel) => {
            const isSelected = selected.includes(channel);
            return (
              <button
                key={channel}
                type="button"
                role="option"
                aria-selected={isSelected}
                onClick={() => {
                  toggleChannel(channel);
                }}
                className={`flex w-full items-center justify-between gap-sm rounded-sm px-[10px] py-2 text-left text-body transition-colors hover:bg-primary-tint ${
                  isSelected ? 'font-medium text-primary-deep' : 'text-ink'
                }`}
                title={channel}
              >
                <span className="min-w-0 truncate">{shortChannel(channel)}</span>
                {isSelected ? <CheckIcon /> : null}
              </button>
            );
          })}
        </div>
      ) : null}
      {limitsOpen ? (
        <AccountLimitsModal
          accountId={account.account_id}
          name={account.name}
          campaignId={campaignId}
          onClose={() => {
            setLimitsOpen(false);
          }}
        />
      ) : null}
      {confirmRemove ? (
        <ConfirmModal
          title={t('neurocomment.modal.neuroAccounts.removeTitle', { name: account.name })}
          body={t('neurocomment.modal.neuroAccounts.removeBody')}
          confirmLabel={t('neurocomment.modal.neuroAccounts.removeConfirm')}
          cancelLabel={t('neurocomment.modal.cancel')}
          onClose={() => {
            setConfirmRemove(false);
          }}
          onConfirm={() => {
            onRemove(account.account_id);
          }}
        />
      ) : null}
    </div>
  );
}

// Design modal: neuro-accounts (L1460-1495) — manage every account in
// neurocommenting: assign an idle account to the campaign, pin a linked account
// to one campaign channel (or "Все каналы" to comment on all), or remove one.
export function NeuroAccountsModal({
  accounts,
  channels = [],
  campaignId,
  onClose,
  onPick,
  onRemove,
  onChannelChange,
  feedback = {},
}: {
  accounts: NeuroAccountRow[];
  channels?: string[];
  // Passed through to the limits modal so a saved cap refreshes this board's cards.
  campaignId?: string | null;
  onClose: () => void;
  onPick: (accountId: string) => void;
  onRemove: (accountId: string) => void;
  onChannelChange: (accountId: string, channels: string[]) => void;
  feedback?: Record<string, 'ok' | 'err'>;
}) {
  const { t } = useTranslation();
  return (
    <Modal
      onClose={onClose}
      className="w-[560px]"
      label={t('neurocomment.modal.neuroAccounts.title')}
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
            <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
            <circle cx="9" cy="7" r="4" />
            <path d="M23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75" />
          </svg>
        </span>
        <div>
          <div className="text-title font-bold text-ink">
            {t('neurocomment.modal.neuroAccounts.title')}
          </div>
          <div className="mt-[2px] text-body text-ink-subtle">
            {t('neurocomment.modal.neuroAccounts.sub', { count: accounts.length })}
          </div>
        </div>
      </div>

      <div className="px-6 pb-4 pt-2">
        {accounts.length > 0 ? (
          accounts.map((account) => (
            <AccountRow
              key={account.account_id}
              account={account}
              channels={channels}
              campaignId={campaignId}
              onPick={onPick}
              onRemove={onRemove}
              onChannelChange={onChannelChange}
              result={feedback[account.account_id]}
            />
          ))
        ) : (
          <div className="px-[10px] py-8 text-center text-lead text-ink-subtle">
            {t('neurocomment.modal.neuroAccounts.empty')}
          </div>
        )}
      </div>

      <div className="flex justify-end border-t border-line-row px-6 pb-5 pt-[14px]">
        <Button variant="primary" onClick={onClose}>
          {t('neurocomment.modal.neuroAccounts.done')}
        </Button>
      </div>
    </Modal>
  );
}
