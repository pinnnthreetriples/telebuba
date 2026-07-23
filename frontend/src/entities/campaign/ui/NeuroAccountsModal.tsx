import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { ConfirmModal, FeedbackMark, Modal } from '@/shared/ui';

export interface NeuroAccountRow {
  account_id: string;
  name: string;
  linked: boolean;
  pinned_channels: string[];
}

// Stable React key for the "all channels" row (an empty subset = serve all).
const ALL_CHANNELS = 'all';

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

function AccountRow({
  account,
  channels,
  onPick,
  onRemove,
  onChannelChange,
  result,
}: {
  account: NeuroAccountRow;
  channels: string[];
  onPick: (accountId: string) => void;
  onRemove: (accountId: string) => void;
  onChannelChange: (accountId: string, channels: string[]) => void;
  result?: 'ok' | 'err';
}) {
  const { t } = useTranslation();
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [open, setOpen] = useState(false);
  const ddRef = useRef<HTMLDivElement>(null);

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
        ? selected[0]
        : t('neurocomment.modal.neuroAccounts.channelsSelected', { count: selected.length });

  // Close the dropdown on any click outside it (mirrors the app's tb-dd menus).
  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e: MouseEvent) => {
      if (ddRef.current && !ddRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', onDown);
    return () => {
      document.removeEventListener('mousedown', onDown);
    };
  }, [open]);

  // Multi-select: toggling a channel keeps the menu open; "Все каналы" clears the
  // whole subset (= all). The empty list is the "serve all channels" sentinel.
  const toggleChannel = (channel: string) => {
    onChannelChange(
      account.account_id,
      selected.includes(channel) ? selected.filter((c) => c !== channel) : [...selected, channel],
    );
  };

  return (
    <div className="flex items-center gap-[10px] border-b border-[#f4f2ef] py-[11px]">
      <FeedbackMark result={result} />
      <span className="min-w-0 flex-1 truncate text-[13px] font-semibold text-ink">
        {account.name}
      </span>
      {account.linked ? (
        // Each linked account gets a ~180px multi-select of the campaign's channels;
        // an empty selection ("Все каналы") = comment on all. Custom tb-dd menu (not a
        // native <select>) so the open list matches the design and allows multi-pick.
        <div ref={ddRef} className="relative w-[180px] shrink-0">
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
            className="tb-time flex w-full items-center justify-between gap-2 rounded-[10px] border border-line-input bg-white px-[11px] py-[8px] text-[12.5px] text-ink"
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
          <div
            role="listbox"
            aria-multiselectable
            className={`tb-dd absolute inset-x-0 top-[calc(100%+5px)] z-20 rounded-[10px] border border-line bg-white p-1 shadow-[0_10px_30px_rgba(11,11,12,0.1)] ${open ? 'open' : ''}`}
          >
            <button
              key={ALL_CHANNELS}
              type="button"
              role="option"
              aria-selected={selected.length === 0}
              onClick={() => {
                onChannelChange(account.account_id, []);
              }}
              className={`flex w-full items-center justify-between gap-2 rounded-[7px] px-[10px] py-2 text-left text-[12.5px] transition-colors hover:bg-[#f2f6ff] ${
                selected.length === 0 ? 'bg-[#f2f6ff] font-semibold text-primary' : 'text-ink'
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
                  className={`flex w-full items-center justify-between gap-2 rounded-[7px] px-[10px] py-2 text-left text-[12.5px] transition-colors hover:bg-[#f2f6ff] ${
                    isSelected ? 'bg-[#f2f6ff] font-semibold text-primary' : 'text-ink'
                  }`}
                >
                  <span className="min-w-0 truncate">{channel}</span>
                  {isSelected ? <CheckIcon /> : null}
                </button>
              );
            })}
          </div>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => {
            onPick(account.account_id);
          }}
          className="w-[180px] shrink-0 rounded-[9px] border border-dashed border-line-strong bg-white px-[11px] py-[8px] text-[12.5px] font-medium text-primary hover:border-primary"
        >
          {t('neurocomment.modal.neuroAccounts.assign')}
        </button>
      )}
      <button
        type="button"
        aria-label={t('neurocomment.modal.neuroAccounts.remove')}
        onClick={() => {
          setConfirmRemove(true);
        }}
        className="flex h-8 w-8 shrink-0 items-center justify-center rounded-[9px] border border-line bg-white text-danger hover:border-[#f0c9c5] hover:bg-danger-tint"
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
      </button>
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
  onClose,
  onPick,
  onRemove,
  onChannelChange,
  feedback = {},
}: {
  accounts: NeuroAccountRow[];
  channels?: string[];
  onClose: () => void;
  onPick: (accountId: string) => void;
  onRemove: (accountId: string) => void;
  onChannelChange: (accountId: string, channels: string[]) => void;
  feedback?: Record<string, 'ok' | 'err'>;
}) {
  const { t } = useTranslation();
  return (
    <Modal onClose={onClose} z={72} className="max-h-[88vh] w-[560px] overflow-y-auto">
      <div className="flex items-center gap-[11px] border-b border-[#f0eeeb] px-6 pb-[15px] pt-5">
        <span className="flex h-[34px] w-[34px] shrink-0 items-center justify-center rounded-[10px] bg-primary-tint text-primary">
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
          <div className="text-[16px] font-bold text-ink">
            {t('neurocomment.modal.neuroAccounts.title')}
          </div>
          <div className="mt-[2px] text-[12.5px] text-ink-subtle">
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
              onPick={onPick}
              onRemove={onRemove}
              onChannelChange={onChannelChange}
              result={feedback[account.account_id]}
            />
          ))
        ) : (
          <div className="px-[10px] py-8 text-center text-[13px] text-ink-subtle">
            {t('neurocomment.modal.neuroAccounts.empty')}
          </div>
        )}
      </div>

      <div className="flex justify-end border-t border-[#f0eeeb] px-6 pb-5 pt-[14px]">
        <button
          type="button"
          onClick={onClose}
          className="rounded-full bg-primary px-[22px] py-[9px] text-[13px] font-semibold text-white"
        >
          {t('neurocomment.modal.neuroAccounts.done')}
        </button>
      </div>
    </Modal>
  );
}
