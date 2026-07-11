import { useTranslation } from 'react-i18next';

import type { CommentRecord, NeurocommentAccountCard } from '@/shared/api';
import { formatLocalTime } from '@/shared/lib';
import { CollapsibleCard } from '@/shared/ui';

// The published-comments feed: one row per posted comment (newest first), so all
// N comments are visible instead of only each account's last one on the board.
// Rows carry account_id; we resolve the human label from the board's cards.
export function CommentFeedCard({
  comments,
  accounts,
  onOpenHistory,
}: {
  comments: CommentRecord[];
  accounts: NeurocommentAccountCard[];
  onOpenHistory: () => void;
}) {
  const { t } = useTranslation();
  const labelOf = new Map(accounts.map((a) => [a.account_id, a.label]));
  return (
    <CollapsibleCard
      defaultOpen
      label={t('neurocomment.feed.title')}
      headerClassName="px-4 py-[13px]"
      bodyClassName="px-[14px] pb-[14px]"
      header={
        <>
          <span className="pl-pulse h-[7px] w-[7px] shrink-0 rounded-full bg-primary" />
          <span className="text-[13px] font-semibold">{t('neurocomment.feed.title')}</span>
          <span className="rounded-full bg-[#f2f1ee] px-2 py-[2px] text-[11px] font-medium text-ink-muted">
            {comments.length}
          </span>
        </>
      }
      trailing={
        <button
          type="button"
          onClick={onOpenHistory}
          className="rounded-full border border-line bg-white px-3 py-[4px] text-[11.5px] font-medium text-primary hover:border-primary"
        >
          {t('neurocomment.feed.history')}
        </button>
      }
    >
      {comments.length === 0 ? (
        <div className="py-6 text-center text-[12.5px] text-ink-subtle">
          {t('neurocomment.feed.empty')}
        </div>
      ) : (
        <div className="tb-scroll max-h-[260px] overflow-y-auto">
          {comments.map((c) => (
            <div
              key={`${c.channel}:${String(c.post_id)}`}
              className="flex items-baseline gap-[10px] border-b border-[#f4f2ef] py-[7px] last:border-b-0 text-[12.5px]"
            >
              <span className="shrink-0 text-ink-subtle">{formatLocalTime(c.created_at)}</span>
              <span className="shrink-0 font-medium">
                {labelOf.get(c.account_id) ?? c.account_id}
              </span>
              <span className="shrink-0 text-primary">{c.channel}</span>
              <span className="min-w-0 flex-1 truncate text-[#5c5c5c]">
                {c.comment_text ?? '—'}
              </span>
            </div>
          ))}
        </div>
      )}
    </CollapsibleCard>
  );
}
