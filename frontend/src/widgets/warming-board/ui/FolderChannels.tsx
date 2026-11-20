import { useTranslation } from 'react-i18next';

import { AccountAvatar, accountDisplayName } from '@/entities/account';
import type { WarmingAccountState } from '@/shared/api';
import { Badge, EmptyState, Icon, Notice } from '@/shared/ui';

// «Каналы» внутри папки. Сервер пока знает про аккаунт только его ПОСЛЕДНИЙ канал,
// поэтому вкладка честно показывает, где аккаунты папки сейчас, а полный список их
// каналов (и свой пул у папки) — следующий этап.
export function FolderChannels({
  accounts,
  channelLabels,
}: {
  accounts: WarmingAccountState[];
  channelLabels: Record<string, string>;
}) {
  const { t } = useTranslation();
  const byChannel = new Map<string, WarmingAccountState[]>();
  const nowhere: WarmingAccountState[] = [];
  for (const account of accounts) {
    const channel = account.last_channel;
    if (!channel) {
      nowhere.push(account);
      continue;
    }
    const list = byChannel.get(channel);
    if (list) list.push(account);
    else byChannel.set(channel, [account]);
  }
  const rows = [...byChannel.entries()].sort((a, b) => b[1].length - a[1].length);

  return (
    <div className="flex flex-col gap-3">
      <Notice tone="info">{t('warming.folders.channels.note')}</Notice>
      {rows.length === 0 ? (
        <EmptyState>{t('warming.folders.channels.none')}</EmptyState>
      ) : (
        <div className="flex flex-col">
          {rows.map(([channel, members]) => (
            <div
              key={channel}
              className="flex items-center gap-3 border-t border-line py-2 first:border-t-0"
            >
              <Icon name="link" size={14} />
              <span className="min-w-0 flex-1 truncate type-body">
                {channelLabels[channel] ?? channel}
              </span>
              <div className="flex items-center gap-1">
                {members.map((account) => (
                  <span key={account.account_id} title={accountDisplayName(account)}>
                    <AccountAvatar
                      account={account}
                      className="size-icon shrink-0 rounded-full"
                      fallbackClassName="text-small font-medium bg-info-tint text-info-strong"
                    />
                  </span>
                ))}
              </div>
              <Badge size="xs">{members.length}</Badge>
            </div>
          ))}
        </div>
      )}
      {nowhere.length > 0 ? (
        <div className="type-small text-content-muted">
          {t('warming.folders.channels.nowhere', { count: nowhere.length })}
        </div>
      ) : null}
    </div>
  );
}
