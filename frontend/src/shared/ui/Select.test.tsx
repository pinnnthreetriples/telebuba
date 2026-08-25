import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import { expectNoAxeViolations } from './axe.test-helpers';
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
  return screen.getByRole('combobox', { name: 'Аккаунт' }).getAttribute('aria-activedescendant');
}

function idOf(name: string): string {
  return screen.getByRole('option', { name }).id;
}

function renderSelect(over: Partial<Parameters<typeof Select>[0]> = {}) {
  const onChange = vi.fn();
  const { container } = render(
    <Select
      value=""
      onChange={onChange}
      options={OPTIONS}
      placeholder="Выберите"
      ariaLabel="Аккаунт"
      {...over}
    />,
  );
  return { onChange, container, trigger: screen.getByRole('combobox', { name: 'Аккаунт' }) };
}

test('the trigger shows the placeholder until an option matches the value', () => {
  const { trigger } = renderSelect();
  expect(trigger).toHaveTextContent('Выберите');
});

test('opens on click and closes again on a second click', async () => {
  const { container, trigger } = renderSelect();
  expect(trigger).toHaveAttribute('aria-expanded', 'false');

  await userEvent.click(trigger);
  expect(trigger).toHaveAttribute('aria-expanded', 'true');
  // Open is the state worth checking: that is when the listbox, its options and the
  // trigger's aria-activedescendant all exist to be pointed at one another.
  await expectNoAxeViolations(container);

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
  expect(screen.getByRole('combobox', { name: 'Аккаунт' })).toHaveTextContent('Boris');
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

// The failure this guards against is the one a roving-focus dropdown has: with real
// focus inside the list, Tab moves it to the next option and a blur handler commits
// whatever it landed on, so leaving the control silently changes the value. Here the
// cursor is an attribute, so Tab is the trigger's own — it leaves the CONTROL, not just
// the trigger, and picks nothing.
//
// The list is OPEN in the state this exercises, which is the only state where it can
// fail: `inert` comes off on open, so the options are ordinary tab stops unless they say
// otherwise, and until they did, this Tab landed on Alisa — one row inside the list.
// Hence the sentinel: `not.toHaveFocus()` on the trigger was true either way and could
// not tell the two apart. Asserting where focus WENT is what makes this test a test.
test('Tab leaves the whole control, not just the trigger, and commits nothing', async () => {
  const onChange = vi.fn();
  render(
    <>
      <Select
        value=""
        onChange={onChange}
        options={OPTIONS}
        placeholder="Выберите"
        ariaLabel="Аккаунт"
      />
      <button type="button">Дальше</button>
    </>,
  );
  const trigger = screen.getByRole('combobox', { name: 'Аккаунт' });
  trigger.focus();

  await userEvent.keyboard('{ArrowDown}{ArrowDown}');
  expect(cursor()).toBe(idOf('Boris'));
  expect(trigger).toHaveAttribute('aria-expanded', 'true');

  await userEvent.tab();

  expect(onChange).not.toHaveBeenCalled();
  expect(screen.getByRole('button', { name: 'Дальше' })).toHaveFocus();
});

// One Tab per option is what an open twenty-row list costs when the rows keep their
// default tab stop, and `inert` cannot help: it is off for exactly as long as the list
// is open. The rows are still programmatically focusable (tabIndex -1, not `inert`),
// which is what the inert test above relies on.
test('an open list adds no tab stops of its own', async () => {
  const { trigger } = renderSelect();
  await userEvent.click(trigger);

  for (const option of screen.getAllByRole('option')) {
    expect(option).toHaveAttribute('tabindex', '-1');
  }
});

// `aria-activedescendant` is a promise that an id resolves, and it resolves in one of
// three places only: a DOM descendant, an `aria-owns` descendant, or inside the
// `aria-controls` target. The trigger is the list's SIBLING, so the first two are out by
// construction and this is the one that has to hold — before it did, the cursor named an
// id no assistive technology would look for, and the whole keyboard suite above passed
// over it, because every one of those tests reads the attribute rather than resolving it.
test('the cursor resolves: aria-controls names the listbox and the cursor is inside it', async () => {
  const { trigger } = renderSelect();
  trigger.focus();
  await userEvent.keyboard('{ArrowDown}');

  const listbox = screen.getByRole('listbox');
  expect(trigger).toHaveAttribute('aria-controls', listbox.id);
  expect(listbox.id).not.toBe('');

  const named = document.getElementById(cursor() ?? '');
  expect(named).toBe(screen.getByRole('option', { name: 'Alisa' }));
  expect(listbox.contains(named)).toBe(true);
});

// And the role that makes the attribute legal at all: `aria-activedescendant` is
// supported on composite roles, not on `button`.
test('the trigger is a combobox, so it may carry a cursor', () => {
  const { trigger } = renderSelect();

  expect(trigger).toHaveAttribute('role', 'combobox');
  expect(trigger).toHaveAttribute('aria-haspopup', 'listbox');
});

// `.tb-dd.open` caps the panel at 240px and scrolls; with DOM focus pinned to the
// trigger the browser never scrolls a row into view on its own, so the cursor walked off
// the bottom of a long list in silence.
//
// happy-dom's `scrollIntoView` is a no-op stub, so this proves the CALL and its
// arguments, not that anything scrolled — the scrolling itself is browser territory.
test('moving the cursor scrolls the row it lands on into view', async () => {
  const scrollIntoView = vi.spyOn(Element.prototype, 'scrollIntoView').mockImplementation(() => {
    /* happy-dom stub; see above */
  });
  const { trigger } = renderSelect();
  trigger.focus();

  await userEvent.keyboard('{ArrowDown}{ArrowDown}');

  const last = scrollIntoView.mock.instances.at(-1);
  expect(last).toBe(screen.getByRole('option', { name: 'Boris' }));
  expect(scrollIntoView).toHaveBeenLastCalledWith({ block: 'nearest' });
  scrollIntoView.mockRestore();
});
