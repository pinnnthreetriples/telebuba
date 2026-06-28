import { useTranslation } from 'react-i18next';

import { WarmingStateBadge } from '@/entities/warming';
import type { WarmingAccountState } from '@/shared/api';

interface WarmingBoardProps {
  idle: WarmingAccountState[];
  warming: WarmingAccountState[];
  onStart: (accountId: string) => void;
  onStop: (accountId: string) => void;
  busyId: string | null;
}

function Card({
  account,
  action,
  actionLabel,
  busy,
}: {
  account: WarmingAccountState;
  action: () => void;
  actionLabel: string;
  busy: boolean;
}) {
  const { t } = useTranslation();
  return (
    <div className="rounded-md border border-line bg-surface p-3">
      <div className="mb-1 flex items-center justify-between">
        <span className="font-mono text-sm text-ink">{account.account_id}</span>
        <WarmingStateBadge state={account.state} />
      </div>
      <div className="mb-2 text-xs text-ink-subtle">
        {t('warming.card.cycles', { count: account.cycles_completed })}
        {account.trust_score !== null && account.trust_score !== undefined
          ? ` · ${t('warming.card.trust')} ${String(account.trust_score)}`
          : ''}
      </div>
      <button
        type="button"
        className="rounded border border-line px-2 py-1 text-xs hover:bg-canvas disabled:opacity-50"
        disabled={busy}
        onClick={action}
      >
        {actionLabel}
      </button>
    </div>
  );
}

export function WarmingBoard({ idle, warming, onStart, onStop, busyId }: WarmingBoardProps) {
  const { t } = useTranslation();
  return (
    <div className="grid gap-4 md:grid-cols-2">
      <section>
        <h2 className="mb-2 text-sm font-medium text-ink-muted">
          {t('warming.column.idle')} ({idle.length})
        </h2>
        <div className="space-y-2">
          {idle.map((account) => (
            <Card
              key={account.account_id}
              account={account}
              action={() => {
                onStart(account.account_id);
              }}
              actionLabel={t('warming.actions.start')}
              busy={busyId === account.account_id}
            />
          ))}
        </div>
      </section>
      <section>
        <h2 className="mb-2 text-sm font-medium text-ink-muted">
          {t('warming.column.warming')} ({warming.length})
        </h2>
        <div className="space-y-2">
          {warming.map((account) => (
            <Card
              key={account.account_id}
              account={account}
              action={() => {
                onStop(account.account_id);
              }}
              actionLabel={t('warming.actions.stop')}
              busy={busyId === account.account_id}
            />
          ))}
        </div>
      </section>
    </div>
  );
}
