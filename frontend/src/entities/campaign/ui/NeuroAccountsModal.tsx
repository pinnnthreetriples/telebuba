import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { ConfirmModal, FeedbackMark, Modal } from '@/shared/ui';

export interface NeuroAccountRow {
  account_id: string;
  phone: string;
  linked: boolean;
  pinned_channel: string | null;
}

// Empty <option> value is the "all channels" sentinel (unpin → channel: null).
const ALL_CHANNELS = '';

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
  onChannelChange: (accountId: string, channel: string | null) => void;
  result?: 'ok' | 'err';
}) {
  const { t } = useTranslation();
  const [confirmRemove, setConfirmRemove] = useState(false);

  // A linked account may be pinned to one campaign channel or left on all of
  // them; an unknown pin (e.g. a channel since removed) is still surfaced.
  const pin = account.pinned_channel;
  const options = pin !== null && !channels.includes(pin) ? [pin, ...channels] : channels;

  return (
    <div className="flex items-center gap-[10px] border-b border-[#f4f2ef] py-[11px]">
      <FeedbackMark result={result} />
      <span className="min-w-0 flex-1 truncate text-[13px] font-semibold text-ink">
        {account.phone}
      </span>
      {account.linked ? (
        // Design gives each linked account a ~180px channel dropdown to redirect
        // it to another campaign channel; "Все каналы" clears the pin.
        <select
          value={pin ?? ALL_CHANNELS}
          aria-label={t('neurocomment.modal.neuroAccounts.channelLabel')}
          onChange={(e) => {
            onChannelChange(
              account.account_id,
              e.target.value === ALL_CHANNELS ? null : e.target.value,
            );
          }}
          className="w-[180px] shrink-0 truncate rounded-[9px] border border-line-input bg-white px-[11px] py-[8px] text-[12.5px] text-ink"
        >
          <option value={ALL_CHANNELS}>{t('neurocomment.modal.neuroAccounts.allChannels')}</option>
          {options.map((channel) => (
            <option key={channel} value={channel}>
              {channel}
            </option>
          ))}
        </select>
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
          title={t('neurocomment.modal.neuroAccounts.removeTitle', { phone: account.phone })}
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
  onChannelChange: (accountId: string, channel: string | null) => void;
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
