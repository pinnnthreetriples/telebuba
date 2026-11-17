import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';

import { spamBudgetStatusQueryOptions } from '@/entities/account';
import { Notice } from '@/shared/ui';

// How often the fleet's probe use is re-read: it moves with every @SpamBot check, and a
// warning that is a minute stale is still a warning.
const REFRESH_MS = 60_000;

// The fleet-level warning on the Accounts page: the last 24 h of @SpamBot probes against the
// caps. Two reasons, either or both: the daily cap is reached (scheduled checks now wait) and
// the operator's own checks pile up (each extra one raises the risk of a limit). The numbers
// and both verdicts come from the backend (`GET /accounts/spam-budget`); nothing renders
// while the fleet is within them.
export function SpamBudgetWarning() {
  const { t } = useTranslation();
  const budget = useQuery({
    ...spamBudgetStatusQueryOptions(),
    refetchInterval: REFRESH_MS,
  });
  const data = budget.data;
  if (!data?.daily_cap_reached && !data?.out_of_turn_warned) return null;

  return (
    <div className="mb-4">
      <Notice tone="warning" role="status" data-testid="spam-budget-warning">
        <p className="m-0 type-body-medium">{t('accounts.spamBudget.title')}</p>
        {data.daily_cap_reached ? (
          <p className="m-0 type-small">
            {t('accounts.spamBudget.dailyCap', { count: data.probes_24h, cap: data.soft_cap })}
          </p>
        ) : null}
        {data.out_of_turn_warned ? (
          <p className="m-0 type-small">
            {t('accounts.spamBudget.outOfTurn', { count: data.out_of_turn_24h })}
          </p>
        ) : null}
      </Notice>
    </div>
  );
}
