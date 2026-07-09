import { type ColumnDef } from '@tanstack/react-table';
import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';

import type { ChallengeRow } from '@/shared/api';
import { formatLocalTime } from '@/shared/lib';
import { DataTable, type DataTableColumnMeta } from '@/shared/ui';

// The captcha queue on the shared DataTable (finding #10): one row per unsolved
// bot-challenge. The account cell resolves the raw account_id to its phone/label
// (finding #8); the action cell retries the pair.
export function CaptchaQueue({
  rows,
  accountLabel,
  onSolve,
}: {
  rows: ChallengeRow[];
  accountLabel: (accountId: string) => string;
  onSolve: (item: ChallengeRow) => void;
}) {
  const { t } = useTranslation();
  const columns = useMemo<ColumnDef<ChallengeRow>[]>(
    () => [
      {
        id: 'account',
        header: t('neurocomment.board.col.account'),
        cell: ({ row }) => (
          <div className="flex min-w-0 items-center gap-[9px]">
            <span className="tb-livedot h-[7px] w-[7px] shrink-0 rounded-full bg-[#e0a82e]" />
            <div className="min-w-0">
              <div className="truncate text-[12.5px] font-semibold text-ink">
                {accountLabel(row.original.account_id)}
              </div>
              <div className="text-[10.5px] text-ink-subtle">
                {row.original.channel} ·{' '}
                {formatLocalTime(row.original.decided_at, { seconds: true })}
              </div>
            </div>
          </div>
        ),
      },
      {
        id: 'action',
        header: '',
        cell: ({ row }) => (
          <button
            type="button"
            onClick={() => {
              onSolve(row.original);
            }}
            className="shrink-0 rounded-full bg-ink px-[13px] py-[6px] text-[11.5px] font-medium text-white"
          >
            {t('neurocomment.captcha.solve')}
          </button>
        ),
        meta: { cellClassName: 'text-right' } satisfies DataTableColumnMeta,
      },
    ],
    [t, accountLabel, onSolve],
  );

  return (
    <div className="tb-scroll overflow-x-auto">
      <DataTable data={rows} columns={columns} />
    </div>
  );
}
