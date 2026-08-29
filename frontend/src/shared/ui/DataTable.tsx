import {
  type ColumnDef,
  flexRender,
  getCoreRowModel,
  getExpandedRowModel,
  type Row,
  useReactTable,
} from '@tanstack/react-table';
import { Fragment, type HTMLAttributes, type ReactNode, useEffect, useRef, useState } from 'react';

import { cn } from '@/shared/lib/cn';

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

// text-left so headers sit directly above their left-aligned cells; a column that wants a
// different alignment sets it via meta.className, and `cn` is what makes that override
// win. It used to win by accident: both classes reached the element and Tailwind happens
// to emit `text-right` after `text-left`, so the column got its way through emit order
// rather than through anyone deciding.
const TH = 'px-lg py-md text-left type-table-header';
const ROW = 'tb-row border-t border-line-row transition-colors';

// Card layout. `tb-row` is reused as-is — its rule is `.tb-row:hover`, which is
// element-agnostic, so cards get the same hover tint for free.
const CARD = 'tb-row overflow-hidden border-t border-line-row px-lg py-lg first:border-t-0';
const CARD_LABEL = 'shrink-0 type-table-header';
const CARD_VALUE = 'min-w-0 break-words text-right text-body text-content-secondary';

// A sub-row that animates its own exit: it outlives `open` going false until
// `.tb-subrow`'s grid-rows transition ends. Removing it in a single frame is what made
// the LAST row of a board jump — that row is the bottom of the document, so losing
// ~270px at once clamps the window scroll and slides the whole card, while the chevron
// is still 420ms into its own spring.
//
// The mount state lives HERE, per row, and not as a "which row is closing" id on the
// table, which was the first shape and was wrong twice over: row ids are index-based, so
// a poll that shortened the data left the id matching a LATER, collapsed row whose
// sub-row then rendered — and stayed focusable — indefinitely; and one id can hold one
// row, so a second toggle inside the 420ms cancelled the first row's exit and brought
// back the very snap this animates away. Per-row state dies with the row.
function SubRow({
  open,
  className,
  // The table layout has to wrap the animating div in its own <tr><td>. Passing that in
  // keeps the frame out of the DOM entirely while the sub-row is unmounted — no stray
  // empty row per record.
  frame,
  children,
}: {
  open: boolean;
  className?: string;
  frame?: (inner: ReactNode) => ReactNode;
  children: ReactNode;
}) {
  const [mounted, setMounted] = useState(open);
  useEffect(() => {
    if (open) setMounted(true);
  }, [open]);
  if (!mounted) return null;
  const inner = (
    <div
      className={cn('tb-subrow', open ? undefined : 'tb-closing', className)}
      // `grid-template-rows` and NOT max-height, deliberately: CollapsibleCard's
      // onTransitionEnd filters on max-height, and a bubbled one from in here would pull
      // its whole body out of the a11y tree. The target check keeps a descendant's
      // transition from unmounting us early.
      onTransitionEnd={(event) => {
        if (!open && event.target === event.currentTarget) setMounted(false);
      }}
    >
      <div>{children}</div>
    </div>
  );
  return frame ? frame(inner) : inner;
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

  // Measured on the wrapper below rather than on the <table>: the table carries
  // `min-w-table`, so measuring it would always read "it fits" and never switch
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
              className={cn(CARD, rowProps?.className)}
            >
              {head.length > 0 ? (
                <div className="flex items-center gap-md">
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
                    className="mt-md flex items-baseline justify-between gap-md first:mt-0"
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
              {renderSubRow ? (
                <SubRow open={row.getIsExpanded()} className="-mx-lg -mb-lg mt-md">
                  {renderSubRow(row)}
                </SubRow>
              ) : null}
            </div>
          );
        })}
      </div>
    );
  }

  return (
    <div ref={box}>
      <table className="w-full min-w-table border-collapse">
        <thead>
          {table.getHeaderGroups().map((headerGroup) => (
            <tr key={headerGroup.id} className="bg-surface">
              {headerGroup.headers.map((header) => (
                <th
                  key={header.id}
                  className={cn(
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
                <tr {...rowProps} className={cn(ROW, rowProps?.className)}>
                  {row.getVisibleCells().map((cell) => (
                    <td
                      key={cell.id}
                      className={cn(
                        'px-lg py-md',
                        (cell.column.columnDef.meta as DataTableColumnMeta)?.cellClassName,
                      )}
                    >
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  ))}
                </tr>
                {renderSubRow ? (
                  <SubRow
                    open={row.getIsExpanded()}
                    frame={(inner) => (
                      <tr>
                        <td colSpan={row.getVisibleCells().length} className="p-0">
                          {inner}
                        </td>
                      </tr>
                    )}
                  >
                    {renderSubRow(row)}
                  </SubRow>
                ) : null}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
