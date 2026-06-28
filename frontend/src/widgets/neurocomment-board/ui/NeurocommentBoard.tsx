import { useTranslation } from 'react-i18next';

import { ChannelStatusBadge } from '@/entities/campaign';
import type { NeurocommentBoard as NeurocommentBoardData } from '@/shared/api';

export function NeurocommentBoard({ board }: { board: NeurocommentBoardData }) {
  const { t } = useTranslation();
  const channels = board.channels ?? [];
  const accounts = board.accounts ?? [];

  return (
    <div className="grid gap-4 md:grid-cols-2">
      <section>
        <h3 className="mb-2 text-sm font-medium text-ink-muted">
          {t('neurocomment.channels.title')} ({channels.length})
        </h3>
        <ul className="space-y-2">
          {channels.map((channel) => (
            <li
              key={channel.channel}
              className="flex items-center justify-between rounded-md border border-line bg-surface px-3 py-2"
            >
              <span className="font-mono text-sm">{channel.channel}</span>
              <span className="flex items-center gap-2">
                <span className="text-xs text-ink-subtle">
                  {channel.ready_accounts}/{channel.total_accounts}
                </span>
                <ChannelStatusBadge status={channel.status} />
              </span>
            </li>
          ))}
        </ul>
      </section>
      <section>
        <h3 className="mb-2 text-sm font-medium text-ink-muted">
          {t('neurocomment.accounts.title')} ({accounts.length})
        </h3>
        <ul className="space-y-2">
          {accounts.map((account) => (
            <li
              key={account.account_id}
              className="rounded-md border border-line bg-surface px-3 py-2 text-sm"
            >
              <div className="flex items-center justify-between">
                <span className="font-mono">{account.account_id}</span>
                <span className="text-xs text-ink-subtle">{account.trust_band}</span>
              </div>
              <div className="text-xs text-ink-subtle">
                {t('neurocomment.accounts.today', { count: account.comments_today })}
              </div>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
