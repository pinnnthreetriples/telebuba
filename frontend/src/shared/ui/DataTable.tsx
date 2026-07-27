import {
  type ColumnDef,
  flexRender,
  getCoreRowModel,
  getExpandedRowModel,
  type Row,
  useReactTable,
} from '@tanstack/react-table';
import { Fragment, type HTMLAttributes, type ReactNode, useSyncExternalStore } from 'react';

// A thin, headless-table wrapper over @tanstack/react-table: one consistent
// `<table>` shell (uppercase header on the surface tint, hover rows) that later
// clusters (logs, neurocomment board, captcha queue) reuse. Layout-agnostic —
// the card/scroll frame belongs to the calling widget. Column meta.className
// (header) and meta.cellClassName (body) let a column steer per-cell styling;
// getRowProps wires row-level behaviour like click-to-open.
export interface DataTableColumnMeta {
  className?: string;
  cellClassName?: string;
  // Card layout only (viewport < CARD_BELOW): puts this column in the card's
  // header row instead of the labelled label/value list beneath it.
  //   'title'   — the row's identity (grows, wraps): phone, @channel, account, time.
  //   'control' — a checkbox / button / chevron / badge (never shrinks), placed in
  //               column order so a *leading* checkbox stays leading.
  // Unset → a labelled row: the column's `header` is the label, its `cell` the value.
  // meta.cellClassName is deliberately NOT applied on the card path: it encodes
  // table-cell geometry (w-px, max-w + nowrap, text-right) that a card must not
  // inherit — w-px would squeeze a chevron to 1px, and a nowrap ellipsis would
  // truncate a comment inside a card where wrapping is the whole point.
  cardSlot?: 'title' | 'control';
}

interface DataTableProps<TData> {
  data: TData[];
  columns: ColumnDef<TData>[];
  // HTMLElement, not HTMLTableRowElement: the same object is spread on a <tr> on a
  // wide viewport and on the card <div> on a narrow one.
  getRowProps?: (row: Row<TData>) => HTMLAttributes<HTMLElement>;
  // When set, a row whose TanStack expanded-state is on renders this full-width
  // beneath it (drive the toggle from a column cell via row.toggleExpanded()).
  renderSubRow?: (row: Row<TData>) => ReactNode;
}

// text-left so headers sit directly above their left-aligned cells; a column that
// wants a different alignment sets it via meta.className (text-right wins over this).
const TH =
  'px-4 py-[11px] text-left text-[11px] font-medium uppercase tracking-[0.04em] text-ink-subtle';
const ROW = 'tb-row border-t border-[#f0eeeb] transition-colors';

// Card layout. `tb-row` is reused as-is — its rule is `.tb-row:hover`, which is
// element-agnostic, so cards get the same hover tint for free.
const CARD = 'tb-row overflow-hidden border-t border-[#f0eeeb] px-4 py-[13px] first:border-t-0';
const CARD_LABEL = 'shrink-0 text-[11px] font-medium uppercase tracking-[0.04em] text-ink-subtle';
const CARD_VALUE = 'min-w-0 break-words text-right text-[12.5px] text-[#3a3a3a]';

// Local, dependency-free class join (avoids a shared/ui → shared/lib → query
// barrel cycle). No tailwind-merge dedupe is needed — callers pass disjoint
// utilities via column meta / getRowProps.
function join(...parts: (string | undefined)[]): string {
  return parts.filter(Boolean).join(' ');
}

// The table/card switch. 880px is the table's own minimum and the shell gives
// content viewport−48px (AppShell: mx-auto max-w-[1340px] px-4/px-6), so 1024 is
// the narrowest viewport where the widest tables fit without the horizontal scroll
// the card layout exists to replace.
const CARD_BELOW = 1024;
const WIDE_MQ = `(min-width: ${String(CARD_BELOW)}px)`;

function subscribeWide(onChange: () => void): () => void {
  const mql = window.matchMedia(WIDE_MQ);
  mql.addEventListener('change', onChange);
  return () => {
    mql.removeEventListener('change', onChange);
  };
}

function getWide(): boolean {
  return window.matchMedia(WIDE_MQ).matches;
}

// A media *query* rather than `hidden lg:table` + `lg:hidden`, so exactly one tree
// is in the DOM. The CSS form renders every cell, event handler and accessible name
// twice; since no stylesheet is loaded under happy-dom, `hidden` is inert there and
// both copies would answer every testing-library query.
function useWide(): boolean {
  return useSyncExternalStore(subscribeWide, getWide);
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

  const wide = useWide();

  if (!wide) {
    // Column id → its header, so a card label can flexRender `header` with a real
    // HeaderContext. Do not pass cell.getContext(): today's headers ignore the
    // context, but a future `({ table }) => …` header would throw.
    const headerById = new Map(
      table.getHeaderGroups().flatMap((group) => group.headers.map((h) => [h.column.id, h])),
    );
    return (
      <div>
        {table.getRowModel().rows.map((row) => {
          const rowProps = getRowProps?.(row);
          const cells = row.getVisibleCells();
          const slotOf = (cell: (typeof cells)[number]) =>
            (cell.column.columnDef.meta as DataTableColumnMeta | undefined)?.cardSlot;
          const head = cells.filter((cell) => slotOf(cell) !== undefined);
          const body = cells.filter((cell) => slotOf(cell) === undefined);
          return (
            <div key={row.id} {...rowProps} className={join(CARD, rowProps?.className)}>
              {head.length > 0 ? (
                <div className="flex items-center gap-[10px]">
                  {head.map((cell) => (
                    <div
                      key={cell.id}
                      className={slotOf(cell) === 'title' ? 'min-w-0 flex-1' : 'shrink-0'}
                    >
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </div>
                  ))}
                </div>
              ) : null}
              {body.map((cell) => {
                const header = headerById.get(cell.column.id);
                return (
                  <div
                    key={cell.id}
                    className="mt-[9px] flex items-baseline justify-between gap-3 first:mt-0"
                  >
                    <span className={CARD_LABEL}>
                      {header
                        ? flexRender(header.column.columnDef.header, header.getContext())
                        : null}
                    </span>
                    <span className={CARD_VALUE}>
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </span>
                  </div>
                );
              })}
              {/* Bled out of the card's padding: sub-row content already carries its
                  own border-t/tint designed to sit flush under a table row. */}
              {renderSubRow && row.getIsExpanded() ? (
                <div className="-mx-4 -mb-[13px] mt-[11px]">{renderSubRow(row)}</div>
              ) : null}
            </div>
          );
        })}
      </div>
    );
  }

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
