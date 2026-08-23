import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import { Select, type SelectOption } from './Select';

const OPTIONS: SelectOption[] = [
  { value: 'a', label: 'Alisa' },
  { value: 'b', label: 'Boris' },
];

const WITH_DISABLED: SelectOption[] = [
  { value: 'a', label: 'Alisa' },
  { value: 'b', label: 'Boris', disabled: true },
  { value: 'c', label: 'Vera' },
];

// The keyboard cursor lives in `aria-activedescendant` on the trigger, pointing at an
// option's id — DOM focus never leaves the trigger, so that attribute IS the cursor.
function cursor(): string | null {
  return screen.getByRole('button', { name: 'Аккаунт' }).getAttribute('aria-activedescendant');
}

function idOf(name: string): string {
  return screen.getByRole('option', { name }).id;
}

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

// The five sites converted off a native <select> had arrow keys for free; losing them
// was the one real regression in that swap. DOM focus stays on the trigger throughout:
// the list is inert while closed, and real focus inside it would fight both that and
// the Modal Tab trap.
test('Down on a closed trigger opens the list on the selected option', async () => {
  const { trigger } = renderSelect({ value: 'b' });
  trigger.focus();

  await userEvent.keyboard('{ArrowDown}');

  expect(trigger).toHaveAttribute('aria-expanded', 'true');
  expect(cursor()).toBe(idOf('Boris'));
});

test('Down on a closed trigger with nothing picked lands on the first option', async () => {
  const { trigger } = renderSelect();
  trigger.focus();

  await userEvent.keyboard('{ArrowDown}');
  expect(trigger).toHaveAttribute('aria-expanded', 'true');
  expect(cursor()).toBe(idOf('Alisa'));
});

test('Down and Up move the cursor, wrapping at both ends', async () => {
  const { trigger } = renderSelect();
  trigger.focus();

  await userEvent.keyboard('{ArrowDown}');
  expect(cursor()).toBe(idOf('Alisa'));

  await userEvent.keyboard('{ArrowDown}');
  expect(cursor()).toBe(idOf('Boris'));

  // Past the last row it wraps to the first, and back again upwards.
  await userEvent.keyboard('{ArrowDown}');
  expect(cursor()).toBe(idOf('Alisa'));

  await userEvent.keyboard('{ArrowUp}');
  expect(cursor()).toBe(idOf('Boris'));
});

test('Home and End jump to the ends of the list', async () => {
  const { trigger } = renderSelect();
  trigger.focus();
  await userEvent.keyboard('{ArrowDown}');

  await userEvent.keyboard('{End}');
  expect(cursor()).toBe(idOf('Boris'));

  await userEvent.keyboard('{Home}');
  expect(cursor()).toBe(idOf('Alisa'));
});

test('moving the cursor steps over a disabled option instead of resting on it', async () => {
  const { trigger } = renderSelect({ options: WITH_DISABLED });
  trigger.focus();

  await userEvent.keyboard('{ArrowDown}');
  expect(cursor()).toBe(idOf('Alisa'));

  // Boris is disabled, so Down goes straight past it.
  await userEvent.keyboard('{ArrowDown}');
  expect(cursor()).toBe(idOf('Vera'));

  await userEvent.keyboard('{ArrowUp}');
  expect(cursor()).toBe(idOf('Alisa'));

  // And the ends skip it too — End cannot land on it either.
  await userEvent.keyboard('{End}');
  expect(cursor()).toBe(idOf('Vera'));
});

test('Enter commits the option under the cursor and closes the list', async () => {
  const { onChange, trigger } = renderSelect();
  trigger.focus();

  await userEvent.keyboard('{ArrowDown}{ArrowDown}');
  await userEvent.keyboard('{Enter}');

  expect(onChange).toHaveBeenCalledWith('b');
  expect(trigger).toHaveAttribute('aria-expanded', 'false');
});

test('Space commits the option under the cursor too', async () => {
  const { onChange, trigger } = renderSelect();
  trigger.focus();

  await userEvent.keyboard('{ArrowDown}');
  await userEvent.keyboard('[Space]');

  expect(onChange).toHaveBeenCalledWith('a');
  expect(trigger).toHaveAttribute('aria-expanded', 'false');
});

// The cursor is only a cursor while the list is open; a closed Select must not claim
// an active descendant that nobody can see.
test('the closed list publishes no active descendant', async () => {
  const { trigger } = renderSelect();
  trigger.focus();

  expect(cursor()).toBeNull();
  await userEvent.keyboard('{ArrowDown}');
  expect(cursor()).not.toBeNull();
  await userEvent.keyboard('{Escape}');
  expect(cursor()).toBeNull();
});
