import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import { Select, type SelectOption } from './Select';

const OPTIONS: SelectOption[] = [
  { value: 'a', label: 'Alisa' },
  { value: 'b', label: 'Boris' },
];

function renderSelect(over: Partial<Parameters<typeof Select>[0]> = {}) {
  const onChange = vi.fn();
  render(
    <Select
      value=""
      onChange={onChange}
      options={OPTIONS}
      placeholder="Выберите"
      ariaLabel="Аккаунт"
      {...over}
    />,
  );
  return { onChange, trigger: screen.getByRole('button', { name: 'Аккаунт' }) };
}

test('the trigger shows the placeholder until an option matches the value', () => {
  const { trigger } = renderSelect();
  expect(trigger).toHaveTextContent('Выберите');
});

test('opens on click and closes again on a second click', async () => {
  const { trigger } = renderSelect();
  expect(trigger).toHaveAttribute('aria-expanded', 'false');

  await userEvent.click(trigger);
  expect(trigger).toHaveAttribute('aria-expanded', 'true');

  await userEvent.click(trigger);
  expect(trigger).toHaveAttribute('aria-expanded', 'false');
});

test('picking an option reports its value and closes the list', async () => {
  const { onChange, trigger } = renderSelect();
  await userEvent.click(trigger);
  await userEvent.click(screen.getByRole('option', { name: 'Boris' }));

  expect(onChange).toHaveBeenCalledWith('b');
  expect(trigger).toHaveAttribute('aria-expanded', 'false');
});

// The picked row is the one marked selected, so a screen reader and the check icon
// agree — and '' is a real value at two call sites, not "nothing picked".
test('the option matching the value is the selected one', () => {
  renderSelect({ value: 'b' });

  expect(screen.getByRole('option', { name: 'Boris' })).toHaveAttribute('aria-selected', 'true');
  expect(screen.getByRole('option', { name: 'Alisa' })).toHaveAttribute('aria-selected', 'false');
  expect(screen.getByRole('button', { name: 'Аккаунт' })).toHaveTextContent('Boris');
});

test('closes on Escape', async () => {
  const { trigger } = renderSelect();
  await userEvent.click(trigger);

  await userEvent.keyboard('{Escape}');
  expect(trigger).toHaveAttribute('aria-expanded', 'false');
});

// Escape while the list is open belongs to the list alone: every modal call site has
// a dialog behind it whose own Escape listener sits on `document`.
test('Escape stops at the open list and never reaches the page behind it', async () => {
  const onEscape = vi.fn();
  document.addEventListener('keydown', onEscape);
  const { trigger } = renderSelect();

  await userEvent.click(trigger);
  await userEvent.keyboard('{Escape}');
  expect(onEscape).not.toHaveBeenCalled();

  // Closed, the same key is nobody's business but the page's.
  await userEvent.keyboard('{Escape}');
  expect(onEscape).toHaveBeenCalledTimes(1);
  document.removeEventListener('keydown', onEscape);
});

test('closes on a click outside', async () => {
  const { trigger } = renderSelect();
  await userEvent.click(trigger);

  await userEvent.click(document.body);
  expect(trigger).toHaveAttribute('aria-expanded', 'false');
});

// .tb-dd collapses VISUALLY only (max-height:0 + opacity:0), so the options stay in
// the DOM and would keep their tab stops. `inert` is what keeps a keyboard operator
// out of a closed list — and out of a surrounding Modal's Tab trap.
test('a closed list is inert, an open one is not', async () => {
  const { trigger } = renderSelect();
  const closed = screen.getByRole('option', { name: 'Boris' });
  closed.focus();
  expect(closed).not.toHaveFocus();

  await userEvent.click(trigger);
  const open = screen.getByRole('option', { name: 'Boris' });
  open.focus();
  expect(open).toHaveFocus();
});

test('a disabled trigger does not open', async () => {
  const { trigger } = renderSelect({ disabled: true });

  await userEvent.click(trigger);
  expect(trigger).toHaveAttribute('aria-expanded', 'false');
});

test('no options renders the empty label instead of a blank panel', async () => {
  const { trigger } = renderSelect({ options: [], emptyLabel: 'Нет аккаунтов' });

  await userEvent.click(trigger);
  expect(screen.getByText('Нет аккаунтов')).toBeInTheDocument();
  expect(screen.queryByRole('option')).not.toBeInTheDocument();
});
