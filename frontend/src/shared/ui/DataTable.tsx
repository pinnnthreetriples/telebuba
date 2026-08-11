import {
  type ColumnDef,
  type ExpandedState,
  flexRender,
  getCoreRowModel,
  getExpandedRowModel,
  type Row,
  useReactTable,
} from '@tanstack/react-table';
import { Fragment, type HTMLAttributes, type ReactNode, useRef, useState } from 'react';

import { useWideContainer } from './useWideViewport';

// A thin, headless-table wrapper over @tanstack/react-table: one consistent
// `<table>` shell (uppercase header on the surface tint, hover rows) that later
// clusters (logs, neurocomment board, captcha queue) reuse. Layout-agnostic —
// the card/scroll frame belongs to the calling widget. Column meta.className
// (header) and meta.cellClassName (body) let a column steer per-cell styling;
// getRowProps wires row-level behaviour like click-to-open.
export interface DataTableColumnMeta {
  className?: string;
  cellClassName?: string;
  // Card layout only: put this column in the card's header row ('title' grows and
  // wraps, 'control' never shrinks and keeps column order so a leading checkbox
  // stays leading) instead of the labelled label/value list beneath it.
  // meta.cellClassName is deliberately NOT applied on the card path: it encodes
  // table-cell geometry — w-px would squeeze a chevron to 1px, and a nowrap ellipsis
  // would truncate a comment inside a card where wrapping is the whole point.
  cardSlot?: 'title' | 'control';
}

interface DataTableProps<TData> {
  data: TData[];
  columns: ColumnDef<TData>[];
  // HTMLElement, not HTMLTableRowElement: the same object is spread on a <tr> in a
  // wide container and on the card <div> in a narrow one.
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

export function DataTable<TData>({
  data,
  columns,
  getRowProps,
  renderSubRow,
}: DataTableProps<TData>) {
  // Expansion is controlled here rather than left to TanStack's internal state for
  // one reason: the exit has to animate. Removing a sub-row in a single frame is
  // what made the LAST row jump — that row sits at the bottom of the document, so
  // losing ~270px at once clamps the window scroll and slides the whole card, while
  // the chevron is still 420ms into its own spring. So the row that just closed is
  // parked in `closing` and keeps rendering (at 0fr) until `.tb-subrow`'s
  // transitionend takes it out.
  const [expanded, setExpanded] = useState<ExpandedState>({});
  const [closing, setClosing] = useState<string | null>(null);
  const table = useReactTable({
    data,
    columns,
    state: { expanded },
    onExpandedChange: (updater) => {
      const next = typeof updater === 'function' ? updater(expanded) : updater;
      // `true` means "everything expanded" — no single row closed, nothing to park.
      setClosing(
        typeof expanded === 'object' && typeof next === 'object'
          ? (Object.keys(expanded).find((id) => expanded[id] && !next[id]) ?? null)
          : null,
      );
      setExpanded(next);
    },
    getCoreRowModel: getCoreRowModel(),
    getExpandedRowModel: getExpandedRowModel(),
    getRowCanExpand: () => renderSubRow !== undefined,
  });

  // The animating wrapper, shared by both layouts so the transition runs on the
  // same element in each; `extra` carries the layout's own frame. It transitions
  // `grid-template-rows` and NOT max-height on purpose: CollapsibleCard's
  // onTransitionEnd filters on max-height, and a bubbled one from in here would
  // pull its whole body out of the a11y tree.
  const subRow = (row: Row<TData>, extra?: string) =>
    renderSubRow && (row.getIsExpanded() || closing === row.id) ? (
      <div
        className={join('tb-subrow', row.getIsExpanded() ? undefined : 'tb-closing', extra)}
        onTransitionEnd={(event) => {
          if (event.target === event.currentTarget) setClosing(null);
        }}
      >
        <div>{renderSubRow(row)}</div>
      </div>
    ) : null;

  // Measured on the wrapper below rather than on the <table>: the table carries
  // `min-w-[880px]`, so measuring it would always read "it fits" and never switch
  // back to cards. Both branches return the same wrapper element in the same
  // position, so React keeps the node — and the ref — across a layout switch.
  const box = useRef<HTMLDivElement>(null);
  const wide = useWideContainer(box);

  if (!wide) {
    // Column id → header, so a card label renders with a real HeaderContext — not
    // cell.getContext(), which today's headers ignore but a future `({ table }) => …`
    // header would choke on. A Map, not getFlatHeaders().find(): the lookup runs once
    // per cell per row, and the logs table is long.
    const headerById = new Map(
      table.getHeaderGroups().flatMap((group) => group.headers.map((h) => [h.column.id, h])),
    );
    // role=list/listitem: the cards are anonymous divs, so without this a screen
    // reader gets one flat run of text with nothing marking where a record ends —
    // the boundary that <tr> used to provide.
    return (
      <div ref={box} role="list">
        {table.getRowModel().rows.map((row) => {
          const rowProps = getRowProps?.(row);
          const cells = row.getVisibleCells();
          const slotOf = (cell: (typeof cells)[number]) =>
            (cell.column.columnDef.meta as DataTableColumnMeta | undefined)?.cardSlot;
          const head = cells.filter((cell) => slotOf(cell) !== undefined);
          const body = cells.filter((cell) => slotOf(cell) === undefined);
          return (
            // role after the spread, like className: it is part of the role="list"
            // parent's structure, so a caller must not be able to clobber it.
            <div
              key={row.id}
              {...rowProps}
              role="listitem"
              className={join(CARD, rowProps?.className)}
            >
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
              {subRow(row, '-mx-4 -mb-[13px] mt-[11px]')}
            </div>
          );
        })}
      </div>
    );
  }

  return (
    <div ref={box}>
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
            const sub = subRow(row);
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
                {sub ? (
                  <tr>
                    <td colSpan={row.getVisibleCells().length} className="p-0">
                      {sub}
                    </td>
                  </tr>
                ) : null}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
