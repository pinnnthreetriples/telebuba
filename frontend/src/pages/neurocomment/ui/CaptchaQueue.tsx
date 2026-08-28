import { type ColumnDef } from '@tanstack/react-table';
import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';

import type { ChallengeRow } from '@/shared/api';
import { formatLocalTime } from '@/shared/lib';
import { DataTable, type DataTableColumnMeta } from '@/shared/ui';

// The captcha queue on the shared DataTable (finding #10): one row per unsolved
// bot-challenge. The account cell resolves the raw account_id to its phone/label
// (finding #8); the second cell says what the engine is about to do about it.
//
// There is no control here any more (#49). The retry used to be a button an operator
// pressed; it is now the sweep's own second and last attempt, so a row is a live status
// and nothing else — the backend drops it from the queue the moment the pair either
// passes or gives up and leaves the chat.
export function CaptchaQueue({
  rows,
  accountLabel,
}: {
  rows: ChallengeRow[];
  accountLabel: (accountId: string) => string;
}) {
  const { t } = useTranslation();
  const columns = useMemo<ColumnDef<ChallengeRow>[]>(
    () => [
      {
        id: 'account',
        header: t('neurocomment.board.col.account'),
        cell: ({ row }) => (
          <div className="flex min-w-0 items-center gap-md">
            <span className="tb-livedot size-dot shrink-0 rounded-full bg-warning-strong" />
            <div className="min-w-0">
              <div className="truncate type-item-title">
                {accountLabel(row.original.account_id)}
              </div>
              <div className="type-caption">
                {row.original.channel} ·{' '}
                {formatLocalTime(row.original.decided_at, { seconds: true })}
              </div>
            </div>
          </div>
        ),
        meta: { cardSlot: 'title' } satisfies DataTableColumnMeta,
      },
      {
        id: 'status',
        // Untitled on screen, but not to a screen reader: the <th> is announced
        // before every cell under it, and an empty one is announced as blank. The
        // column already has a name in the table this one mirrors, so it reuses it.
        header: () => <span className="sr-only">{t('neurocomment.board.col.status')}</span>,
        // "within five minutes" is the sweep interval, which is the honest ceiling: the
        // retry fires on the first tick after the failure, so it lands anywhere from
        // seconds to that. A countdown would have to promise an exact moment the rule
        // deliberately does not have.
        cell: () => (
          <span className="shrink-0 type-caption">{t('neurocomment.captcha.retrying')}</span>
        ),
        meta: { cellClassName: 'text-right', cardSlot: 'control' } satisfies DataTableColumnMeta,
      },
    ],
    [t, accountLabel],
  );

  return (
    <div className="tb-scroll overflow-x-auto">
      <DataTable data={rows} columns={columns} />
    </div>
  );
}
