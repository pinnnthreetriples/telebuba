import { useTranslation } from 'react-i18next';

import type { WarmingChannel } from '@/shared/api';
import { Badge, FeedbackMark, Icon, IconButton } from '@/shared/ui';

// The language and country an account matches its phone against, e.g. "ES · MX".
// Nothing at all while both are unknown: an empty tag would read as a label that says
// nothing, and an unlabelled channel is simply everyone's fallback.
function channelTag(channel: WarmingChannel): string | null {
  const parts = [channel.language?.toUpperCase(), channel.country].filter(Boolean);
  return parts.length ? parts.join(' · ') : null;
}

export function WarmingChannelPill({
  channel,
  feedback,
  onRemove,
}: {
  channel: WarmingChannel;
  feedback?: 'ok' | 'err';
  onRemove: (channel: string) => void;
}) {
  const { t } = useTranslation();
  const tag = channelTag(channel);
  return (
    <Badge size="md" className="gap-2 border border-line text-content-secondary">
      <FeedbackMark result={feedback} />
      {channel.channel}
      {tag ? (
        <span className="type-small text-content-muted" title={t('warming.channels.languageTag')}>
          {tag}
        </span>
      ) : null}
      <IconButton
        size="sm"
        shape="circle"
        aria-label={t('warming.channels.remove')}
        onClick={() => {
          onRemove(channel.channel);
        }}
      >
        <Icon name="close" size={16} />
      </IconButton>
    </Badge>
  );
}
