import { type ColumnDef } from '@tanstack/react-table';
import { useQuery } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { neurocommentCommentsQueryOptions } from '@/entities/campaign';
import type { CommentRecord, NeurocommentAccountCard } from '@/shared/api';
import { formatLocalTime } from '@/shared/lib';
import { DataTable, type DataTableColumnMeta, Modal } from '@/shared/ui';

const PAGE_SIZE = 50;

// Full paginated published-comment history (all time, newest first) — the board
// feed shows only the last 24h. Cursor-stack paging mirrors LogsPage; account
// labels resolve from the board's cards, as CommentFeedCard does.
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
          className: 'w-[130px]',
          cellClassName: 'font-mono text-[12px] text-ink-subtle',
        } satisfies DataTableColumnMeta,
      },
      {
        id: 'account',
        header: () => t('neurocomment.history.col.account'),
        cell: ({ row }) => labelOf.get(row.original.account_id) ?? row.original.account_id,
        meta: {
          className: 'w-[150px]',
          cellClassName: 'text-[12.5px] font-medium text-ink',
        } satisfies DataTableColumnMeta,
      },
      {
        id: 'channel',
        header: () => t('neurocomment.history.col.channel'),
        cell: ({ row }) => row.original.channel,
        meta: {
          className: 'w-[150px]',
          cellClassName: 'text-[12.5px] text-primary',
        } satisfies DataTableColumnMeta,
      },
      {
        id: 'text',
        header: () => t('neurocomment.history.col.text'),
        cell: ({ row }) => row.original.comment_text ?? '—',
        meta: { cellClassName: 'text-[12.5px] text-[#3a3a3a]' } satisfies DataTableColumnMeta,
      },
    ],
    [t, labelOf],
  );

  return (
    <Modal onClose={onClose} z={72} className="max-h-[88vh] w-[760px] overflow-y-auto">
      <div className="border-b border-[#f0eeeb] px-6 pb-[15px] pt-5">
        <div className="text-[16px] font-bold text-ink">{t('neurocomment.history.title')}</div>
      </div>

      <div className="px-6 pb-4 pt-3">
        {isPending ? (
          <p className="py-10 text-center text-[13px] text-ink-muted">
            {t('neurocomment.history.loading')}
          </p>
        ) : isError ? (
          <p role="alert" className="py-10 text-center text-[13px] text-danger">
            {t('neurocomment.history.error')}
          </p>
        ) : items.length === 0 ? (
          <div className="py-12 text-center text-[13px] text-ink-subtle">
            {t('neurocomment.history.empty')}
          </div>
        ) : (
          <div className="overflow-hidden rounded-2xl border border-line bg-white">
            <div className="tb-scroll overflow-x-auto">
              <DataTable data={items} columns={columns} />
            </div>
          </div>
        )}
      </div>

      <div className="flex items-center justify-between border-t border-[#f0eeeb] px-6 pb-5 pt-[14px]">
        <div className="flex gap-2">
          <button
            type="button"
            disabled={!hasPrev}
            onClick={() => {
              setCursorStack((stack) => stack.slice(0, -1));
            }}
            className="rounded-full border border-line bg-white px-4 py-[7px] text-[13px] disabled:opacity-50"
          >
            {t('neurocomment.history.prev')}
          </button>
          <button
            type="button"
            disabled={!hasNext}
            onClick={() => {
              setCursorStack((stack) => [...stack, data?.next_cursor ?? null]);
            }}
            className="rounded-full border border-line bg-white px-4 py-[7px] text-[13px] disabled:opacity-50"
          >
            {t('neurocomment.history.next')}
          </button>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="rounded-full bg-primary px-[22px] py-[9px] text-[13px] font-semibold text-white"
        >
          {t('neurocomment.history.done')}
        </button>
      </div>
    </Modal>
  );
}
