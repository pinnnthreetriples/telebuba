import { createRef } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';

import * as ds from '@/shared/design-system';
import * as ui from './index';

afterEach(() => {
  vi.restoreAllMocks();
  if (ds.componentSettings) {
    ds.componentSettings.controls.height.md = 'control';
    ds.componentSettings.inset.rowX = 'lg';
    ds.componentSettings.controls.gap = 'tight';
  }
  if (ds.layoutSettings) ds.layoutSettings.sectionGap.default = 'lg';
});

test('layout className keeps the default card inset and title role', () => {
  render(
    <ui.Card title="Settings" className="grid gap-lg" data-testid="card">
      body
    </ui.Card>,
  );
  expect(screen.getByTestId('card')).toHaveClass('px-xl', 'py-xl', 'grid', 'gap-lg');
  expect(screen.getByText('Settings')).toHaveClass('type-compact-title');
});

test('card padding presets are explicit and appearance is independent', () => {
  render(
    <ui.Card padding="compact" appearance="canvas" data-testid="card">
      body
    </ui.Card>,
  );
  expect(screen.getByTestId('card')).toHaveClass('p-lg', 'bg-canvas');
  expect(screen.getByTestId('card')).not.toHaveClass('px-xl');
});

test('collapsible layout additions keep the central header and body insets', () => {
  render(
    <ui.CollapsibleCard
      header="Details"
      label="Toggle"
      defaultOpen
      headerClassName="justify-between"
      bodyClassName="grid"
      headerDivider
    >
      body
    </ui.CollapsibleCard>,
  );
  expect(screen.getByText('Details').parentElement).toHaveClass(
    'px-lg',
    'py-lg',
    'border-b',
    'border-line-row',
  );
  expect(screen.getByText('body')).toHaveClass('px-lg', 'pb-lg', 'grid');
});

test('changing a central control height updates both rendered button and input', () => {
  expect(ds.componentSettings).toBeDefined();
  ds.componentSettings.controls.height.md = 'touch';
  render(
    <>
      <ui.Button>Save</ui.Button>
      <ui.Input aria-label="Name" />
    </>,
  );
  expect(screen.getByRole('button', { name: 'Save' })).toHaveClass('h-touch');
  expect(screen.getByRole('textbox', { name: 'Name' })).toHaveClass('h-touch');
});

test('changing a central row inset updates table cells and stat tiles', () => {
  expect(ds.componentSettings).toBeDefined();
  ds.componentSettings.inset.rowX = 'xl';
  expect(ds.tableCell()).toContain('px-xl');
  expect(ds.statTile()).toContain('px-xl');
});

test('modal parts preserve their slots and setting rows connect labels', () => {
  expect(ui.ModalHeader).toBeTypeOf('function');
  expect(ui.SettingRow).toBeTypeOf('function');
  render(
    <>
      <ui.ModalHeader>Title</ui.ModalHeader>
      <ui.ModalBody>Body</ui.ModalBody>
      <ui.ModalFooter>Actions</ui.ModalFooter>
      <ui.SettingRow label="Limit" hint="Per day" htmlFor="limit">
        <ui.Input id="limit" />
      </ui.SettingRow>
    </>,
  );
  expect(screen.getByText('Title')).toHaveClass('px-2xl', 'pt-xl', 'pb-lg', 'border-b');
  expect(screen.getByText('Body')).toHaveClass('px-2xl', 'py-xl');
  expect(screen.getByText('Actions')).toHaveClass('px-2xl', 'py-lg', 'border-t');
  expect(screen.getByRole('textbox', { name: 'Limit' })).toHaveAttribute('id', 'limit');
  expect(screen.getByText('Per day')).toHaveClass('type-caption');
});

test('search input forwards focus and reports edits and clear actions', () => {
  expect(ui.SearchInput).toBeTypeOf('function');
  const ref = createRef<HTMLInputElement>();
  const change = vi.fn();
  const clear = vi.fn();
  render(
    <ui.SearchInput
      variant="header"
      ref={ref}
      value="query"
      aria-label="Search"
      onChange={change}
      onClear={clear}
      clearLabel="Clear search"
    />,
  );
  ref.current?.focus();
  expect(screen.getByRole('textbox', { name: 'Search' })).toHaveFocus();
  expect(screen.getByRole('textbox', { name: 'Search' }).parentElement).toHaveClass(
    'h-tile',
    'rounded-full',
  );
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'new' } });
  expect(change).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole('button', { name: 'Clear search' }));
  expect(clear).toHaveBeenCalledOnce();
});

test('layout configuration affects page frames and section stacks', () => {
  expect(ds.layoutSettings).toBeDefined();
  ds.layoutSettings.sectionGap.default = 'xl';
  render(
    <ui.PageFrame data-testid="page">
      <ui.SectionStack data-testid="sections">content</ui.SectionStack>
    </ui.PageFrame>,
  );
  expect(screen.getByTestId('page')).toHaveClass('max-w-page');
  expect(screen.getByTestId('sections')).toHaveClass('gap-xl');
});

test('layout escape hatches cannot replace the selected card and collapsible insets', () => {
  render(
    <>
      <ui.Card data-testid="protected" className="p-0">
        card
      </ui.Card>
      <ui.CollapsibleCard
        header="Protected header"
        label="Toggle"
        defaultOpen
        headerClassName="p-0"
        bodyClassName="p-0"
      >
        protected body
      </ui.CollapsibleCard>
    </>,
  );
  expect(screen.getByTestId('protected')).toHaveClass('px-xl', 'py-xl');
  expect(screen.getByText('Protected header').parentElement).toHaveClass('px-lg', 'py-lg');
  expect(screen.getByText('protected body')).toHaveClass('px-lg', 'pb-lg');
});

test('stat tile tones preserve the current fleet statistics paints', () => {
  render(
    <>
      <ui.StatTile label="Active" value={12} tone="success" />
      <ui.StatTile label="Idle" value={3} tone="warning" />
      <ui.StatTile label="Warming" value={4} tone="action" />
      <ui.StatTile label="Problem" value={1} tone="danger" />
    </>,
  );
  expect(screen.getByText('12')).toHaveClass('text-success-deep');
  expect(screen.getByText('3')).toHaveClass('text-warning-deep');
  expect(screen.getByText('4')).toHaveClass('text-action-primary');
  expect(screen.getByText('1')).toHaveClass('text-danger');
});

test('button content gap and field icon insets are named requests', () => {
  render(
    <>
      <ui.Button contentGap="roomy">With icon</ui.Button>
      <ui.Input aria-label="Handle" inset="leading" />
      <ui.Input aria-label="Password" inset="trailing" />
    </>,
  );
  expect(screen.getByRole('button', { name: 'With icon' })).toHaveClass('gap-sm');
  expect(screen.getByRole('textbox', { name: 'Handle' })).toHaveClass('pl-page');
  expect(screen.getByRole('textbox', { name: 'Password' })).toHaveClass('field-end-inset');
});

test('changing the default central content gap updates a rendered button', () => {
  ds.componentSettings.controls.gap = 'lg';
  render(<ui.Button>Save gap</ui.Button>);
  expect(screen.getByRole('button', { name: 'Save gap' })).toHaveClass('gap-lg');
});
