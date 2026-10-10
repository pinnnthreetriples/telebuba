// One link in a list of chats, with the badge that says what kind of link it is.
import { useTranslation } from 'react-i18next';

import type { ChatBroadcastResolvedTarget } from '@/shared/api';
import { Badge, Icon, IconButton } from '@/shared/ui';

import { guessKind } from '../../model/collections';

export function TargetChip({
  raw,
  resolved,
  onRemove,
}: {
  raw: string;
  resolved: ChatBroadcastResolvedTarget | undefined;
  onRemove: () => void;
}) {
  const { t } = useTranslation();
  const kind = resolved?.kind ?? guessKind(raw);
  return (
    <span className="inline-flex items-center gap-2 rounded-full border border-line bg-canvas px-3 py-1 text-body text-content-secondary">
      {raw}
      {kind === 'invite' ? (
        <span title={t('chatBroadcast.settings.chats.inviteHint')}>
          <Badge tone="info">{t('chatBroadcast.settings.chats.invite')}</Badge>
        </span>
      ) : null}
      {kind === 'folder' ? (
        <span title={t('chatBroadcast.settings.chats.folderHint')}>
          <Badge tone="info">
            {resolved?.folder_count === null || resolved?.folder_count === undefined
              ? t('chatBroadcast.settings.chats.folderUnknown')
              : t('chatBroadcast.settings.chats.folder', { count: resolved.folder_count })}
          </Badge>
        </span>
      ) : null}
      <IconButton
        size="sm"
        shape="circle"
        aria-label={t('chatBroadcast.settings.chats.remove', { target: raw })}
        onClick={onRemove}
      >
        <Icon name="close" size={16} />
      </IconButton>
    </span>
  );
}
