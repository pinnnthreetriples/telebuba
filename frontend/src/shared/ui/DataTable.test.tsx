import type { ColumnDef } from '@tanstack/react-table';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, test, vi } from 'vitest';

import { expectNoAxeViolations } from './axe.test-helpers';
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
    // A column with nothing to show in its header still needs a name: the <th> is
    // read out before every cell under it, and an empty one is announced as blank.
    // sr-only, so the column stays visually untitled — the same trick AppNav uses
    // for the connection status text.
    header: () => <span className="sr-only">Подробности</span>,
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

// happy-dom reports every box as 0×0, which is why the layout switch falls back to the
// viewport query there — so the container path needs the measurement stubbed.
function setContainerWidth(width: number): void {
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(width);
}

afterEach(() => {
  // Back to happy-dom's default so a later test added to this file gets the table
  // branch unless it opts out. (Vitest isolates per file, so no other suite cares.)
  setViewport(1024);
  vi.restoreAllMocks();
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
test('a wide viewport renders the table, and each cell exactly once', async () => {
  const { container } = renderTable();

  expect(screen.getByRole('table')).toBeInTheDocument();
  expect(screen.getAllByText('first-row')).toHaveLength(1);
  // Separately from text: no duplicated *accessible names* either.
  expect(screen.getAllByLabelText('Выбрать first-row')).toHaveLength(1);
  // A labelled column's header appears once (as a <th>), not once per row.
  expect(screen.getAllByText('ЗАМЕТКА')).toHaveLength(1);
  await expectNoAxeViolations(container);
});

// The reported bug: on the neurocomment screen a tablet-width viewport passes the
// viewport query while the board's own column is 620px and the config rail 340px, so
// the table rendered and scrolled sideways over its 880px floor inside the card.
test('a narrow container renders cards even when the viewport is wide', () => {
  setViewport(1440);
  setContainerWidth(620);
  renderTable();

  expect(screen.queryByRole('table')).toBeNull();
  expect(screen.getAllByRole('listitem')).toHaveLength(DATA.length);
});

test('a container as wide as the table renders the table', () => {
  setContainerWidth(900);
  renderTable();

  expect(screen.getByRole('table')).toBeInTheDocument();
});

// The card branch is a second tree this component renders, not a variant of the
// first — no <table>, no <th>, list semantics instead — so it gets its own pass.
test('a narrow viewport replaces the table with one card per row', async () => {
  setViewport(375);
  const { container } = renderTable();

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
  await expectNoAxeViolations(container);
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

  // Collapsing no longer removes the sub-row in the same commit: it stays, marked
  // `tb-closing`, so grid-template-rows can animate 1fr → 0fr. Dropping ~270px in a
  // single frame is what made the LAST row of a board jump — that row sits at the
  // bottom of the document, so the browser clamped the window scroll and slid the
  // whole card. No CSS runs in happy-dom, hence the hand-fired transitionend.
  await userEvent.click(toggle);
  const closing = screen.getByText('подробности first-row').closest('.tb-subrow');
  expect(closing).toHaveClass('tb-closing');

  fireEvent.transitionEnd(closing as HTMLElement);
  await waitFor(() => {
    expect(screen.queryByText('подробности first-row')).toBeNull();
  });
});

// The reported bug's own layout: the desktop table, and its LAST row — the one whose
// sub-row is the bottom of the document.
test('the last table row keeps its closing sub-row until the transition ends', async () => {
  renderTable();

  const toggle = screen.getByLabelText('Раскрыть second-row');
  await userEvent.click(toggle);
  expect(await screen.findByText('подробности second-row')).toBeInTheDocument();

  await userEvent.click(toggle);
  const closing = screen.getByText('подробности second-row').closest('.tb-subrow');
  expect(closing).toHaveClass('tb-closing');

  fireEvent.transitionEnd(closing as HTMLElement);
  await waitFor(() => {
    expect(screen.queryByText('подробности second-row')).toBeNull();
  });
});

// Regression: the exit state used to be one "which row is closing" id on the table, so
// the next expansion change wiped it and the row still animating was dropped in a single
// frame — the exact snap the animation exists to remove, on the commonest gesture there
// is (browsing accounts one after another).
test('opening another row does not cut short the one still closing', async () => {
  renderTable();

  const first = screen.getByLabelText('Раскрыть first-row');
  await userEvent.click(first);
  await userEvent.click(first);
  // second-row's sub-row opens while first-row's is mid-exit
  await userEvent.click(screen.getByLabelText('Раскрыть second-row'));

  const closing = screen.getByText('подробности first-row').closest('.tb-subrow');
  expect(closing).toHaveClass('tb-closing');
  expect(screen.getByText('подробности second-row').closest('.tb-subrow')).not.toHaveClass(
    'tb-closing',
  );
});

// Regression: with a shared closing id and index-based row ids, a poll that shortened the
// data left the id matching a LATER, collapsed row — whose sub-row then rendered, and
// stayed focusable, for the life of the component.
test('a row that unmounts mid-close leaves no ghost behind', async () => {
  const { rerender } = render(
    <DataTable
      data={DATA}
      columns={COLUMNS}
      renderSubRow={(row) => <div>подробности {row.original.name}</div>}
    />,
  );

  const second = screen.getByLabelText('Раскрыть second-row');
  await userEvent.click(second);
  await userEvent.click(second);
  expect(screen.getByText('подробности second-row')).toBeInTheDocument();

  // the poll returns one row, dropping the row that was mid-exit…
  rerender(
    <DataTable
      data={[DATA[0]!]}
      columns={COLUMNS}
      renderSubRow={(row) => <div>подробности {row.original.name}</div>}
    />,
  );
  expect(screen.queryByText('подробности second-row')).toBeNull();

  // …and when it grows back, the row at that index is collapsed, with nothing revealed.
  rerender(
    <DataTable
      data={DATA}
      columns={COLUMNS}
      renderSubRow={(row) => <div>подробности {row.original.name}</div>}
    />,
  );
  expect(screen.getByText('second-row')).toBeInTheDocument();
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
