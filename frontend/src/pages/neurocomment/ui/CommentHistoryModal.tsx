import { useQuery } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { neurocommentCommentsQueryOptions } from '@/entities/campaign';
import type { CommentRecord, NeurocommentAccountCard } from '@/shared/api';
import { formatLocalTime } from '@/shared/lib';
import { Badge, CloseButton, EmptyState, Icon, IconButton, Modal, ModalHeader } from '@/shared/ui';

const PAGE_SIZE = 50;
const DAY_MS = 24 * 60 * 60 * 1000;

type DayGroup = { key: string; date: Date; items: CommentRecord[] };

// The page arrives newest first, so consecutive runs of one local calendar day are
// the whole day on this page — no sort, just a split where the date changes.
function groupByDay(items: CommentRecord[]): DayGroup[] {
  const groups: DayGroup[] = [];
  for (const item of items) {
    const date = new Date(item.created_at);
    const key = date.toDateString();
    const last = groups.at(-1);
    if (last?.key === key) last.items.push(item);
    else groups.push({ key, date, items: [item] });
  }
  return groups;
}

function startOfDay(date: Date): number {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
}

// Full paginated published-comment history (all time, newest first) — the board's
// per-account feed shows only the last 24h. Cursor-stack paging mirrors LogsPage;
// account labels resolve from the board's cards. Rows are grouped under a day line
// so the time column can stay a bare HH:MM.
export function CommentHistoryModal({
  campaignId,
  accounts,
  onClose,
}: {
  campaignId: string;
  accounts: NeurocommentAccountCard[];
  onClose: () => void;
}) {
  const { t, i18n } = useTranslation();
  const [cursorStack, setCursorStack] = useState<(string | null)[]>([null]);
  const cursor = cursorStack[cursorStack.length - 1] ?? undefined;

  const { data, isPending, isError } = useQuery(
    neurocommentCommentsQueryOptions({
      path: { campaign_id: campaignId },
      query: { cursor, limit: PAGE_SIZE },
    }),
  );

  const hasPrev = cursorStack.length > 1;
  const hasNext = Boolean(data?.next_cursor);
  const page = cursorStack.length;

  const labelOf = useMemo(() => new Map(accounts.map((a) => [a.account_id, a.label])), [accounts]);
  const groups = useMemo(() => groupByDay(data?.items ?? []), [data]);

  const dayLabel = (date: Date): string => {
    const diff = Math.round((startOfDay(new Date()) - startOfDay(date)) / DAY_MS);
    if (diff === 0) return t('neurocomment.history.today');
    if (diff === 1) return t('neurocomment.history.yesterday');
    return new Intl.DateTimeFormat(i18n.language, {
      weekday: 'short',
      day: 'numeric',
      month: 'long',
    }).format(date);
  };

  return (
    <Modal onClose={onClose} size="panel" label={t('neurocomment.history.title')}>
      <ModalHeader title={t('neurocomment.history.title')} subtitle={t('neurocomment.history.sub')}>
        <div className="ml-auto flex shrink-0 items-center gap-1">
          <IconButton
            size="sm"
            disabled={!hasPrev}
            aria-label={t('neurocomment.history.prev')}
            onClick={() => {
              setCursorStack((stack) => stack.slice(0, -1));
            }}
          >
            <Icon name="chevron-left" size={14} />
          </IconButton>
          <span className="min-w-badge text-center font-mono type-small text-content-subtle tabular-nums">
            {page}
          </span>
          <IconButton
            size="sm"
            disabled={!hasNext}
            aria-label={t('neurocomment.history.next')}
            onClick={() => {
              setCursorStack((stack) => [...stack, data?.next_cursor ?? null]);
            }}
          >
            <Icon name="chevron-right" size={14} />
          </IconButton>
          <CloseButton
            className="ml-2"
            aria-label={t('neurocomment.history.done')}
            onClick={onClose}
          />
        </div>
      </ModalHeader>

      <div className="px-6 pb-6 pt-2">
        {isPending ? (
          <EmptyState size="xl">{t('neurocomment.history.loading')}</EmptyState>
        ) : isError ? (
          <EmptyState role="alert" size="xl" tone="danger">
            {t('neurocomment.history.error')}
          </EmptyState>
        ) : groups.length === 0 ? (
          <EmptyState size="xl">{t('neurocomment.history.empty')}</EmptyState>
        ) : (
          groups.map((group) => (
            <section key={group.key} aria-label={dayLabel(group.date)}>
              <h3 className="flex items-baseline justify-between border-b border-canvas pb-1 pt-4 type-small-medium text-content-secondary">
                <span className="first-letter:uppercase">{dayLabel(group.date)}</span>
                <span className="font-mono text-content-subtle tabular-nums">
                  {group.items.length}
                </span>
              </h3>
              <ul>
                {group.items.map((c) => (
                  <HistoryRow
                    key={`${c.channel}:${String(c.post_id)}:${c.account_id}`}
                    comment={c}
                    account={labelOf.get(c.account_id) ?? c.account_id}
                    deletedLabel={t('neurocomment.feed.deleted')}
                  />
                ))}
              </ul>
            </section>
          ))
        )}
      </div>
    </Modal>
  );
}

function HistoryRow({
  comment,
  account,
  deletedLabel,
}: {
  comment: CommentRecord;
  account: string;
  deletedLabel: string;
}) {
  const deleted = Boolean(comment.deleted_at);
  return (
    <li className="flex gap-3 border-b border-canvas py-2 last:border-b-0">
      <time
        dateTime={comment.created_at}
        className="w-action shrink-0 font-mono type-small text-content-subtle tabular-nums"
      >
        {formatLocalTime(comment.created_at)}
      </time>
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 items-center gap-1 type-small text-content-subtle">
          <span className="truncate font-medium text-content-secondary">{account}</span>
          <span aria-hidden="true">·</span>
          <span className="truncate text-action-primary">{comment.channel}</span>
          {deleted ? (
            <Badge tone="danger" className="ml-auto shrink-0">
              {deletedLabel}
            </Badge>
          ) : null}
        </div>
        <p
          className={`line-clamp-3 break-words type-body ${deleted ? 'text-content-subtle' : 'text-content-primary'}`}
        >
          {/* <del>, not a bare strike: the struck text is announced as removed content. */}
          {deleted ? (
            <del className="line-through">{comment.comment_text ?? '—'}</del>
          ) : (
            (comment.comment_text ?? '—')
          )}
        </p>
      </div>
    </li>
  );
}
