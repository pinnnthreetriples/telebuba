import type { ColumnDef } from '@tanstack/react-table';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, test } from 'vitest';

import { DataTable, type DataTableColumnMeta } from './DataTable';

interface Item {
  id: string;
  name: string;
  note: string;
}

const DATA: Item[] = [
  { id: 'a', name: 'first-row', note: 'note-a' },
  { id: 'b', name: 'second-row', note: 'note-b' },
];

// One fixture covering all three cardSlot states plus renderSubRow.
const COLUMNS: ColumnDef<Item>[] = [
  {
    id: 'name',
    header: 'ИМЯ',
    cell: ({ row }) => row.original.name,
    meta: { cardSlot: 'title' } satisfies DataTableColumnMeta,
  },
  {
    id: 'note',
    header: 'ЗАМЕТКА',
    cell: ({ row }) => row.original.note,
  },
  {
    id: 'select',
    // A select-all control in the header, mirroring DiscoveryResults: as a card
    // label this would become one select-all per card.
    header: () => <input type="checkbox" aria-label="Выбрать все" />,
    cell: ({ row }) => <input type="checkbox" aria-label={`Выбрать ${row.original.name}`} />,
    meta: { cardSlot: 'control' } satisfies DataTableColumnMeta,
  },
  {
    id: 'expander',
    header: '',
    cell: ({ row }) => (
      <button
        type="button"
        aria-label={`Раскрыть ${row.original.name}`}
        onClick={row.getToggleExpandedHandler()}
      >
        ▾
      </button>
    ),
    meta: { cardSlot: 'control', cellClassName: 'w-px' } satisfies DataTableColumnMeta,
  },
];

// happy-dom re-evaluates matchMedia().matches on setViewport but does NOT dispatch
// the MQL `change` event, so this has to run before render — a resize afterwards
// would not re-render.
function setViewport(width: number): void {
  (
    window as unknown as { happyDOM: { setViewport: (v: { width: number }) => void } }
  ).happyDOM.setViewport({ width });
}

afterEach(() => {
  // Back to happy-dom's default so a later test added to this file gets the table
  // branch unless it opts out. (Vitest isolates per file, so no other suite cares.)
  setViewport(1024);
});

function renderTable(extra?: Partial<Parameters<typeof DataTable<Item>>[0]>) {
  return render(
    <DataTable
      data={DATA}
      columns={COLUMNS}
      renderSubRow={(row) => <div>подробности {row.original.name}</div>}
      {...extra}
    />,
  );
}

// The load-bearing assertion of this file: it is what fails if anyone swaps the JS
// switch for `hidden lg:table` + `lg:hidden`, which would put both trees in the DOM.
test('a wide viewport renders the table, and each cell exactly once', () => {
  renderTable();

  expect(screen.getByRole('table')).toBeInTheDocument();
  expect(screen.getAllByText('first-row')).toHaveLength(1);
  // Separately from text: no duplicated *accessible names* either.
  expect(screen.getAllByLabelText('Выбрать first-row')).toHaveLength(1);
  // A labelled column's header appears once (as a <th>), not once per row.
  expect(screen.getAllByText('ЗАМЕТКА')).toHaveLength(1);
});

test('a narrow viewport replaces the table with one card per row', () => {
  setViewport(375);
  renderTable();

  expect(screen.queryByRole('table')).toBeNull();
  // 'title' column: value present, no label.
  expect(screen.getByText('first-row')).toBeInTheDocument();
  expect(screen.queryByText('ИМЯ')).toBeNull();
  // Unset column: both label and value, once per row.
  expect(screen.getAllByText('ЗАМЕТКА')).toHaveLength(DATA.length);
  expect(screen.getByText('note-a')).toBeInTheDocument();
  // 'control' column: its header never becomes a per-card label.
  expect(screen.queryByLabelText('Выбрать все')).toBeNull();
  expect(screen.getAllByLabelText(/^Выбрать /)).toHaveLength(DATA.length);
});

// Cards are anonymous divs; without list semantics a screen reader gets one flat run
// of text with nothing marking where a record ends — what <tr> used to provide.
test('cards expose record boundaries as list items', () => {
  setViewport(375);
  renderTable();

  expect(screen.getByRole('list')).toBeInTheDocument();
  expect(screen.getAllByRole('listitem')).toHaveLength(DATA.length);
});

test('the expander still toggles, and the sub-row renders inside its own card', async () => {
  setViewport(375);
  renderTable();

  const toggle = screen.getByLabelText('Раскрыть first-row');
  await userEvent.click(toggle);

  const subRow = await screen.findByText('подробности first-row');
  // Scoped to the card that owns the expander, not to a <tr>.
  expect(toggle.closest('div.tb-row')).toContainElement(subRow);
  expect(screen.queryByText('подробности second-row')).toBeNull();
});

test('getRowProps reaches the card', async () => {
  setViewport(375);
  const clicked: string[] = [];
  renderTable({
    getRowProps: (row) => ({
      className: 'cursor-pointer',
      onClick: () => clicked.push(row.original.id),
    }),
  });

  const card = screen.getByText('first-row').closest('div.tb-row');
  expect(card).toHaveClass('cursor-pointer');
  await userEvent.click(card as HTMLElement);
  expect(clicked).toEqual(['a']);
});
