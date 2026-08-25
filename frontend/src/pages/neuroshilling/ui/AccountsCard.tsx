import { useTranslation } from 'react-i18next';

import type { NeuroshillingBoardAccount } from '@/shared/api';
import { Badge, Button, CollapsibleCard, HelpHint } from '@/shared/ui';

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
      headerClassName="px-lg py-lg"
      bodyClassName="px-lg pb-lg"
      header={<span className="text-lead font-semibold">{t('neuroshilling.accounts.title')}</span>}
    >
      <div className="mb-md flex items-center gap-sm">
        <span className="rounded-full border border-line bg-canvas px-md py-xs text-tiny font-medium text-ink-body">
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
        <div className="mb-md flex flex-wrap gap-sm">
          {accounts.map((account) => (
            <Badge size="md" className="border border-line text-ink-body" key={account.account_id}>
              {account.title}
            </Badge>
          ))}
        </div>
      ) : (
        <div className="mb-md text-body text-ink-subtle">{t('neuroshilling.accounts.none')}</div>
      )}

      <Button variant="dashed" size="block" onClick={onPick}>
        {t('neuroshilling.accounts.pick')}
      </Button>
    </CollapsibleCard>
  );
}
