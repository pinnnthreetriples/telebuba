import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type OnChangeFn,
  type RowSelectionState,
  type SortingState,
} from '@tanstack/react-table';
import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';

import { StatusBadge } from '@/entities/account';
import type { AccountRead } from '@/shared/api';
import { cn } from '@/shared/lib';

const columnHelper = createColumnHelper<AccountRead>();

interface AccountsTableProps {
  data: AccountRead[];
  sorting: SortingState;
  onSortingChange: OnChangeFn<SortingState>;
  rowSelection: RowSelectionState;
  onRowSelectionChange: OnChangeFn<RowSelectionState>;
  onCheck: (accountId: string) => void;
  onDelete: (accountId: string) => void;
  busyId: string | null;
}

export function AccountsTable({
  data,
  sorting,
  onSortingChange,
  rowSelection,
  onRowSelectionChange,
  onCheck,
  onDelete,
  busyId,
}: AccountsTableProps) {
  const { t } = useTranslation();

  const columns = useMemo(
    () => [
      columnHelper.display({
        id: 'select',
        header: ({ table }) => (
          <input
            type="checkbox"
            aria-label={t('accounts.table.selectAll')}
            checked={table.getIsAllRowsSelected()}
            onChange={table.getToggleAllRowsSelectedHandler()}
          />
        ),
        cell: ({ row }) => (
          <input
            type="checkbox"
            aria-label={t('accounts.table.selectRow')}
            checked={row.getIsSelected()}
            onChange={row.getToggleSelectedHandler()}
          />
        ),
      }),
      columnHelper.accessor('account_id', {
        header: t('accounts.table.account'),
        cell: ({ row }) => (
          <div>
            <div className="font-mono text-sm text-ink">{row.original.account_id}</div>
            {row.original.label ? (
              <div className="text-xs text-ink-subtle">{row.original.label}</div>
            ) : null}
          </div>
        ),
      }),
      columnHelper.accessor('status', {
        header: t('accounts.table.status'),
        cell: ({ getValue }) => <StatusBadge status={getValue()} />,
      }),
      columnHelper.accessor((row) => row.username ?? row.phone ?? '', {
        id: 'telegram',
        header: t('accounts.table.telegram'),
        cell: ({ getValue }) => <span className="text-sm text-ink-muted">{getValue() || '—'}</span>,
      }),
      columnHelper.accessor('proxy_country_code', {
        header: t('accounts.table.country'),
        cell: ({ getValue }) => {
          const code = getValue();
          return code ? <span className={cn('fi', `fi-${code.toLowerCase()}`)} /> : <span>—</span>;
        },
      }),
      columnHelper.accessor('last_checked_at', {
        header: t('accounts.table.lastChecked'),
        cell: ({ getValue }) => (
          <span className="text-sm text-ink-muted">{getValue() ?? t('accounts.never')}</span>
        ),
      }),
      columnHelper.display({
        id: 'actions',
        header: '',
        cell: ({ row }) => (
          <div className="flex justify-end gap-2">
            <button
              type="button"
              className="rounded border border-line px-2 py-1 text-xs hover:bg-canvas disabled:opacity-50"
              disabled={busyId === row.original.account_id}
              onClick={() => {
                onCheck(row.original.account_id);
              }}
            >
              {t('accounts.actions.check')}
            </button>
            <button
              type="button"
              className="rounded border border-danger/30 px-2 py-1 text-xs text-danger hover:bg-danger-tint disabled:opacity-50"
              disabled={busyId === row.original.account_id}
              onClick={() => {
                onDelete(row.original.account_id);
              }}
            >
              {t('accounts.actions.delete')}
            </button>
          </div>
        ),
      }),
    ],
    [t, onCheck, onDelete, busyId],
  );

  const table = useReactTable({
    data,
    columns,
    state: { sorting, rowSelection },
    onSortingChange,
    onRowSelectionChange,
    getRowId: (row) => row.account_id,
    enableRowSelection: true,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  return (
    <div className="overflow-x-auto rounded-md border border-line bg-surface">
      <table className="w-full border-collapse text-left">
        <thead>
          {table.getHeaderGroups().map((group) => (
            <tr key={group.id} className="border-b border-line">
              {group.headers.map((header) => (
                <th
                  key={header.id}
                  className={cn(
                    'px-4 py-2 text-xs font-medium text-ink-muted',
                    header.column.getCanSort() && 'cursor-pointer select-none',
                  )}
                  onClick={header.column.getToggleSortingHandler()}
                >
                  {flexRender(header.column.columnDef.header, header.getContext())}
                  {{ asc: ' ↑', desc: ' ↓' }[header.column.getIsSorted() as string] ?? ''}
                </th>
              ))}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row) => (
            <tr key={row.id} className="border-b border-line last:border-0 hover:bg-canvas/50">
              {row.getVisibleCells().map((cell) => (
                <td key={cell.id} className="px-4 py-3">
                  {flexRender(cell.column.columnDef.cell, cell.getContext())}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
