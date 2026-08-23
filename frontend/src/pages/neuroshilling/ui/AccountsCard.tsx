import { useTranslation } from 'react-i18next';

import type { NeuroshillingBoardAccount } from '@/shared/api';
import { CollapsibleCard, HelpHint } from '@/shared/ui';

// The campaign's roster: how many accounts are on it, which ones, and the way in
// to the picker. Editing happens in the modal, which saves the whole roster once.
export function AccountsCard({
  accounts,
  onPick,
}: {
  accounts: NeuroshillingBoardAccount[];
  onPick: () => void;
}) {
  const { t } = useTranslation();
  return (
    <CollapsibleCard
      defaultOpen
      label={t('neuroshilling.accounts.title')}
      headerClassName="px-4 py-[15px]"
      bodyClassName="px-4 pb-[15px]"
      header={
        <span className="text-[13px] font-semibold">{t('neuroshilling.accounts.title')}</span>
      }
    >
      <div className="mb-[10px] flex items-center gap-[7px]">
        <span className="rounded-full border border-line bg-track px-[11px] py-[4px] text-[11px] font-medium text-ink-body">
          {t('neuroshilling.accounts.selected', { count: accounts.length })}
        </span>
        {/* The hint carries two rules. The picker enforces the second one — a held
            account's row is disabled there, with the holder named under it — and
            nothing enforces the first: a roster of one saves, and it is Start that
            refuses to run it (LaunchCard's blockers, `not_enough_accounts` on the
            server). */}
        <HelpHint text={t('neuroshilling.accounts.hint')} />
      </div>

      {accounts.length > 0 ? (
        <div className="mb-[10px] flex flex-wrap gap-[7px]">
          {accounts.map((account) => (
            <span
              key={account.account_id}
              className="inline-flex items-center rounded-full border border-line bg-track px-[11px] py-[5px] text-[12.5px] text-ink-body"
            >
              {account.title}
            </span>
          ))}
        </div>
      ) : (
        <div className="mb-[10px] text-[12.5px] text-ink-subtle">
          {t('neuroshilling.accounts.none')}
        </div>
      )}

      <button
        type="button"
        onClick={onPick}
        className="flex w-full items-center justify-center gap-[5px] rounded-lg border border-dashed border-primary-line bg-white py-[9px] text-[12.5px] font-medium text-primary hover:border-primary hover:bg-primary-wash"
      >
        {t('neuroshilling.accounts.pick')}
      </button>
    </CollapsibleCard>
  );
}
