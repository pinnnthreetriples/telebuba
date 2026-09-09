import { useTranslation } from 'react-i18next';

import { mutationErrorText } from '@/shared/lib';
import { Icon, Spinner } from '@/shared/ui';

import type { BulkProfileRow } from './useBulkProfile';

// The run view of a bulk edit: one line per account, in the order the batch
// walks them. Shared by every bulk tab — the rows are the same four states
// whatever was applied.
//
// A refused account keeps its reason on screen rather than only in the global
// toast: a toast for account 7 of 17 is gone by the time the batch ends, and
// "which ones failed, and why" is the whole question afterwards.
export function BulkProgress({
  rows,
  label,
}: {
  rows: BulkProfileRow[];
  label: (accountId: string) => string;
}) {
  const { t } = useTranslation();
  const done = rows.filter((row) => row.state === 'ok' || row.state === 'error').length;
  const failed = rows.filter((row) => row.state === 'error').length;

  return (
    <div className="flex flex-col gap-md">
      <div className="flex items-center justify-between gap-md">
        <span className="type-label">
          {t('accounts.bulk.progress', { done, total: rows.length })}
        </span>
        {failed > 0 && (
          <span className="type-caption text-danger">
            {t('accounts.bulk.failedCount', { n: failed })}
          </span>
        )}
      </div>
      <div className="overflow-hidden rounded-lg border border-line">
        {rows.map((row) => (
          <div
            key={row.accountId}
            className="flex items-center gap-md border-b border-line-row px-md py-sm last:border-b-0"
          >
            <span className="flex size-glyph shrink-0 items-center justify-center">
              {row.state === 'running' ? (
                <Spinner />
              ) : row.state === 'ok' ? (
                <Icon name="check" size={16} className="stroke-success-deep" />
              ) : row.state === 'error' ? (
                <Icon name="x-circle" size={16} className="stroke-danger" />
              ) : (
                <span className="size-dot rounded-full bg-line-strong" />
              )}
            </span>
            <span className="min-w-0 flex-1 truncate type-item-title">{label(row.accountId)}</span>
            <span className="shrink-0 truncate type-caption">
              {row.state === 'error'
                ? mutationErrorText(row.error)
                : row.state === 'ok'
                  ? t('accounts.bulk.rowOk')
                  : row.state === 'running'
                    ? t('accounts.bulk.rowRunning')
                    : t('accounts.bulk.rowQueued')}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
