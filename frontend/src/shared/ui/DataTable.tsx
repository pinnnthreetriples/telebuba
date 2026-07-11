import {
  type ColumnDef,
  flexRender,
  getCoreRowModel,
  getExpandedRowModel,
  type Row,
  useReactTable,
} from '@tanstack/react-table';
import { Fragment, type HTMLAttributes, type ReactNode } from 'react';

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
  // When set, a row whose TanStack expanded-state is on renders this full-width
  // beneath it (drive the toggle from a column cell via row.toggleExpanded()).
  renderSubRow?: (row: Row<TData>) => ReactNode;
}

// text-left so headers sit directly above their left-aligned cells; a column that
// wants a different alignment sets it via meta.className (text-right wins over this).
const TH =
  'px-4 py-[11px] text-left text-[11px] font-medium uppercase tracking-[0.04em] text-ink-subtle';
const ROW = 'tb-row border-t border-[#f0eeeb] transition-colors';

// Local, dependency-free class join (avoids a shared/ui → shared/lib → query
// barrel cycle). No tailwind-merge dedupe is needed — callers pass disjoint
// utilities via column meta / getRowProps.
function join(...parts: (string | undefined)[]): string {
  return parts.filter(Boolean).join(' ');
}

export function DataTable<TData>({
  data,
  columns,
  getRowProps,
  renderSubRow,
}: DataTableProps<TData>) {
  const table = useReactTable({
    data,
    columns,
    getCoreRowModel: getCoreRowModel(),
    getExpandedRowModel: getExpandedRowModel(),
    getRowCanExpand: () => renderSubRow !== undefined,
  });

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
            <Fragment key={row.id}>
              <tr {...rowProps} className={join(ROW, rowProps?.className)}>
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
              {renderSubRow && row.getIsExpanded() ? (
                <tr>
                  <td colSpan={row.getVisibleCells().length} className="p-0">
                    {renderSubRow(row)}
                  </td>
                </tr>
              ) : null}
            </Fragment>
          );
        })}
      </tbody>
    </table>
  );
}
