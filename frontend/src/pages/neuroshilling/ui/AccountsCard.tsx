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
        <span className="rounded-full border border-line bg-[#f4f3f0] px-[11px] py-[4px] text-[11.5px] font-medium text-[#3a3a3a]">
          {t('neuroshilling.accounts.selected', { count: accounts.length })}
        </span>
        {/* The hint carries the two rules the picker enforces but cannot explain:
            a dialogue needs two voices, and a held account is unavailable. */}
        <HelpHint text={t('neuroshilling.accounts.hint')} />
      </div>

      {accounts.length > 0 ? (
        <div className="mb-[10px] flex flex-wrap gap-[7px]">
          {accounts.map((account) => (
            <span
              key={account.account_id}
              className="inline-flex items-center rounded-full border border-line bg-[#f4f3f0] px-[11px] py-[5px] text-[12px] text-[#3a3a3a]"
            >
              {account.title}
            </span>
          ))}
        </div>
      ) : (
        <div className="mb-[10px] text-[12px] text-ink-subtle">
          {t('neuroshilling.accounts.none')}
        </div>
      )}

      <button
        type="button"
        onClick={onPick}
        className="flex w-full items-center justify-center gap-[5px] rounded-[10px] border border-dashed border-[#c7d6f0] bg-white py-[9px] text-[12.5px] font-medium text-primary hover:border-primary hover:bg-[#f2f6ff]"
      >
        {t('neuroshilling.accounts.pick')}
      </button>
    </CollapsibleCard>
  );
}
