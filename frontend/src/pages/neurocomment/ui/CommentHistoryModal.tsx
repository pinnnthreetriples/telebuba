import { type ColumnDef } from '@tanstack/react-table';
import { useQuery } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { neurocommentCommentsQueryOptions } from '@/entities/campaign';
import type { CommentRecord, NeurocommentAccountCard } from '@/shared/api';
import { formatLocalTime } from '@/shared/lib';
import { Badge, Button, Card, DataTable, Modal, type DataTableColumnMeta } from '@/shared/ui';

const PAGE_SIZE = 50;

// Full paginated published-comment history (all time, newest first) — the board's
// per-account feed shows only the last 24h. Cursor-stack paging mirrors LogsPage;
// account labels resolve from the board's cards.
export function CommentHistoryModal({
  campaignId,
  accounts,
  onClose,
}: {
  campaignId: string;
  accounts: NeurocommentAccountCard[];
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [cursorStack, setCursorStack] = useState<(string | null)[]>([null]);
  const cursor = cursorStack[cursorStack.length - 1] ?? undefined;

  const { data, isPending, isError } = useQuery(
    neurocommentCommentsQueryOptions({
      path: { campaign_id: campaignId },
      query: { cursor, limit: PAGE_SIZE },
    }),
  );

  const items = data?.items ?? [];
  const hasPrev = cursorStack.length > 1;
  const hasNext = Boolean(data?.next_cursor);

  const labelOf = useMemo(() => new Map(accounts.map((a) => [a.account_id, a.label])), [accounts]);

  const columns = useMemo<ColumnDef<CommentRecord>[]>(
    () => [
      {
        id: 'time',
        header: () => t('neurocomment.history.col.time'),
        cell: ({ row }) => formatLocalTime(row.original.created_at, { seconds: true }),
        meta: {
          className: 'w-stamp',
          cellClassName: 'font-mono text-body text-ink-subtle',
          cardSlot: 'title',
        } satisfies DataTableColumnMeta,
      },
      {
        id: 'account',
        header: () => t('neurocomment.history.col.account'),
        cell: ({ row }) => labelOf.get(row.original.account_id) ?? row.original.account_id,
        meta: {
          className: 'w-col',
          cellClassName: 'text-body font-medium text-ink',
        } satisfies DataTableColumnMeta,
      },
      {
        id: 'channel',
        header: () => t('neurocomment.history.col.channel'),
        cell: ({ row }) => row.original.channel,
        meta: {
          className: 'w-col',
          cellClassName: 'text-body text-primary',
        } satisfies DataTableColumnMeta,
      },
      {
        id: 'text',
        header: () => t('neurocomment.history.col.text'),
        cell: ({ row }) => {
          const text = row.original.comment_text ?? '—';
          if (!row.original.deleted_at) return text;
          return (
            <span className="inline-flex items-center gap-sm">
              <span className="text-ink-subtle line-through">{text}</span>
              <Badge tone="danger">{t('neurocomment.feed.deleted')}</Badge>
            </span>
          );
        },
        meta: { cellClassName: 'text-body text-ink-body' } satisfies DataTableColumnMeta,
      },
    ],
    [t, labelOf],
  );

  return (
    <Modal onClose={onClose} className="w-table" label={t('neurocomment.history.title')}>
      <div className="border-b border-line-row px-2xl pb-lg pt-xl">
        <div className="text-title font-bold text-ink">{t('neurocomment.history.title')}</div>
      </div>

      <div className="px-2xl pb-lg pt-md">
        {isPending ? (
          <p className="py-[40px] text-center text-lead text-ink-muted">
            {t('neurocomment.history.loading')}
          </p>
        ) : isError ? (
          <p role="alert" className="py-[40px] text-center text-lead text-danger">
            {t('neurocomment.history.error')}
          </p>
        ) : items.length === 0 ? (
          <div className="py-[48px] text-center text-lead text-ink-subtle">
            {t('neurocomment.history.empty')}
          </div>
        ) : (
          <Card className="overflow-hidden">
            <div className="tb-scroll overflow-x-auto">
              <DataTable data={items} columns={columns} />
            </div>
          </Card>
        )}
      </div>

      <div className="flex items-center justify-between border-t border-line-row px-2xl pb-xl pt-lg">
        <div className="flex gap-sm">
          <button
            type="button"
            disabled={!hasPrev}
            onClick={() => {
              setCursorStack((stack) => stack.slice(0, -1));
            }}
            className="rounded-full border border-line bg-white px-lg py-sm text-lead disabled:opacity-50"
          >
            {t('neurocomment.history.prev')}
          </button>
          <button
            type="button"
            disabled={!hasNext}
            onClick={() => {
              setCursorStack((stack) => [...stack, data?.next_cursor ?? null]);
            }}
            className="rounded-full border border-line bg-white px-lg py-sm text-lead disabled:opacity-50"
          >
            {t('neurocomment.history.next')}
          </button>
        </div>
        <Button variant="primary" onClick={onClose}>
          {t('neurocomment.history.done')}
        </Button>
      </div>
    </Modal>
  );
}
