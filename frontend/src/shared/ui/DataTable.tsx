import {
  type ColumnDef,
  flexRender,
  getCoreRowModel,
  type Row,
  useReactTable,
} from '@tanstack/react-table';
import { type HTMLAttributes } from 'react';

// A thin, headless-table wrapper over @tanstack/react-table: one consistent
// `<table>` shell (uppercase header on the surface tint, hover rows) that later
// clusters (logs, neurocomment board, captcha queue) reuse. Layout-agnostic —
// the card/scroll frame belongs to the calling widget. Column meta.className
// (header) and meta.cellClassName (body) let a column steer per-cell styling;
// getRowProps wires row-level behaviour like click-to-open.
export interface DataTableColumnMeta {
  className?: string;
  cellClassName?: string;
}

interface DataTableProps<TData> {
  data: TData[];
  columns: ColumnDef<TData>[];
  getRowProps?: (row: Row<TData>) => HTMLAttributes<HTMLTableRowElement>;
}

const TH = 'px-4 py-[11px] text-[11px] font-medium uppercase tracking-[0.04em] text-ink-subtle';
const ROW = 'tb-row border-t border-[#f0eeeb] transition-colors';

// Local, dependency-free class join (avoids a shared/ui → shared/lib → query
// barrel cycle). No tailwind-merge dedupe is needed — callers pass disjoint
// utilities via column meta / getRowProps.
function join(...parts: (string | undefined)[]): string {
  return parts.filter(Boolean).join(' ');
}

export function DataTable<TData>({ data, columns, getRowProps }: DataTableProps<TData>) {
  const table = useReactTable({ data, columns, getCoreRowModel: getCoreRowModel() });

  return (
    <table className="w-full min-w-[880px] border-collapse">
      <thead>
        {table.getHeaderGroups().map((headerGroup) => (
          <tr key={headerGroup.id} className="bg-surface">
            {headerGroup.headers.map((header) => (
              <th
                key={header.id}
                className={join(
                  TH,
                  (header.column.columnDef.meta as DataTableColumnMeta)?.className,
                )}
              >
                {flexRender(header.column.columnDef.header, header.getContext())}
              </th>
            ))}
          </tr>
        ))}
      </thead>
      <tbody>
        {table.getRowModel().rows.map((row) => {
          const rowProps = getRowProps?.(row);
          return (
            <tr key={row.id} {...rowProps} className={join(ROW, rowProps?.className)}>
              {row.getVisibleCells().map((cell) => (
                <td
                  key={cell.id}
                  className={join(
                    'px-4 py-3',
                    (cell.column.columnDef.meta as DataTableColumnMeta)?.cellClassName,
                  )}
                >
                  {flexRender(cell.column.columnDef.cell, cell.getContext())}
                </td>
              ))}
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
