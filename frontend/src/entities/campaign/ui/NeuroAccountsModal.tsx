import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { ConfirmModal, FeedbackMark, Modal } from '@/shared/ui';

export interface NeuroAccountRow {
  account_id: string;
  phone: string;
  channel: string | null;
}

function AccountRow({
  account,
  onPick,
  onRemove,
  result,
}: {
  account: NeuroAccountRow;
  onPick: (accountId: string) => void;
  onRemove: (accountId: string) => void;
  result?: 'ok' | 'err';
}) {
  const { t } = useTranslation();
  const [confirmRemove, setConfirmRemove] = useState(false);

  return (
    <div className="flex items-center gap-[10px] border-b border-[#f4f2ef] py-[11px]">
      <FeedbackMark result={result} />
      <span className="min-w-0 flex-1 truncate text-[13px] font-semibold text-ink">
        {account.phone}
      </span>
      {account.channel !== null ? (
        <span className="w-[180px] shrink-0 truncate rounded-[9px] border border-line-input bg-[#f6f5f2] px-[11px] py-[8px] text-[12.5px] text-ink-subtle">
          {account.channel}
        </span>
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
// neurocommenting: assign an idle account to the campaign, or remove one.
// Channel pairing itself is automatic (onboard_campaign cross-joins every
// assigned account against the campaign's channels) — there is no backend
// concept of pinning one account to one channel, so this only offers assign /
// remove, not a per-channel picker.
export function NeuroAccountsModal({
  accounts,
  onClose,
  onPick,
  onRemove,
  feedback = {},
}: {
  accounts: NeuroAccountRow[];
  onClose: () => void;
  onPick: (accountId: string) => void;
  onRemove: (accountId: string) => void;
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
              onPick={onPick}
              onRemove={onRemove}
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
