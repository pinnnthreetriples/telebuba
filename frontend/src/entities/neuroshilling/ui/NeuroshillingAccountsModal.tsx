import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { NeuroshillingBoardAccount } from '@/shared/api';
import { Modal } from '@/shared/ui';

// The picker edits a DRAFT and saves it once, when the operator says so. The
// obvious alternative — one request per click, the way the neurocomment picker
// works — cannot be used here: the roster travels inside the whole-form
// `updateNeuroshillingCampaign` body, so two quick clicks would send two bodies
// both derived from the same pre-click board and the second would silently undo
// the first.
export function NeuroshillingAccountsModal({
  accounts,
  onClose,
  onSave,
}: {
  accounts: NeuroshillingBoardAccount[];
  onClose: () => void;
  onSave: (accountIds: string[]) => void;
}) {
  const { t } = useTranslation();
  // Seeded from the pool ONCE, at mount, and the operator's from then on: a later
  // `accounts` (the board is refetched on every log frame) must not move rows they
  // have already clicked. The page mounts this dialog only after the board has
  // landed, so there is no earlier pool for the seed to miss.
  const [picked, setPicked] = useState(
    () => new Set(accounts.filter((account) => account.assigned).map((a) => a.account_id)),
  );

  // Only «done» reaches this. The backdrop, Escape and «cancel» drop the draft,
  // which is the dialog's only undo: the save replaces the WHOLE roster, and a
  // held account taken off it cannot be picked up again here.
  const commit = () => {
    onSave([...picked]);
    onClose();
  };

  const toggle = (accountId: string) => {
    setPicked((current) => {
      const next = new Set(current);
      if (!next.delete(accountId)) next.add(accountId);
      return next;
    });
  };

  // Who is holding this account, named when the holder is a campaign we can name.
  // `busy_owner` is already cleared by the board for accounts THIS campaign holds.
  const heldBy = (account: NeuroshillingBoardAccount): string | null => {
    if (!account.busy_owner) return null;
    const owner = t(`neuroshilling.modal.accounts.busy.${account.busy_owner}`);
    return account.busy_campaign_name
      ? t('neuroshilling.modal.accounts.busyNamed', { owner, name: account.busy_campaign_name })
      : owner;
  };

  return (
    <Modal onClose={onClose} className="w-[560px]" label={t('neuroshilling.modal.accounts.title')}>
      <div className="border-b border-line-row px-6 pb-[15px] pt-5">
        <div className="text-[16px] font-bold text-ink">
          {t('neuroshilling.modal.accounts.title')}
        </div>
        <div className="mt-[2px] text-[12.5px] text-ink-subtle">
          {t('neuroshilling.modal.accounts.sub', { count: accounts.length })}
        </div>
      </div>

      <div className="px-6 pb-4 pt-2">
        {accounts.map((account) => {
          const isPicked = picked.has(account.account_id);
          const held = heldBy(account);
          return (
            <div key={account.account_id} className="border-b border-[#f4f2ef] py-[11px]">
              <div className="flex flex-wrap items-center gap-[10px]">
                <span className="min-w-0 flex-1 truncate text-[13px] font-semibold text-ink">
                  {account.title}
                </span>
                <button
                  type="button"
                  // A held account cannot be added, but one already on the roster
                  // can always be taken off it — that is how a stuck hold is undone.
                  disabled={held !== null && !isPicked}
                  onClick={() => {
                    toggle(account.account_id);
                  }}
                  className={`w-full shrink-0 rounded-md border px-[11px] py-[8px] text-[12.5px] font-medium disabled:opacity-50 sm:w-[180px] ${
                    isPicked
                      ? 'border-danger-line bg-danger-tint text-danger'
                      : 'border-dashed border-line-strong bg-white text-primary hover:border-primary'
                  }`}
                >
                  {isPicked
                    ? t('neuroshilling.modal.accounts.remove')
                    : t('neuroshilling.modal.accounts.add')}
                </button>
              </div>
              {held ? <div className="mt-[6px] text-[11px] text-ink-subtle">{held}</div> : null}
            </div>
          );
        })}
        {accounts.length === 0 ? (
          <div className="px-[10px] py-8 text-center text-[13px] text-ink-subtle">
            {t('neuroshilling.modal.accounts.empty')}
          </div>
        ) : null}
      </div>

      <div className="flex justify-end gap-2 border-t border-line-row px-6 pb-5 pt-[14px]">
        <button
          type="button"
          onClick={onClose}
          className="rounded-full border border-line-input bg-white px-[22px] py-[9px] text-[13px] font-semibold text-ink"
        >
          {t('neuroshilling.modal.accounts.cancel')}
        </button>
        <button
          type="button"
          onClick={commit}
          className="rounded-full bg-primary px-[22px] py-[9px] text-[13px] font-semibold text-white"
        >
          {t('neuroshilling.modal.accounts.done')}
        </button>
      </div>
    </Modal>
  );
}
