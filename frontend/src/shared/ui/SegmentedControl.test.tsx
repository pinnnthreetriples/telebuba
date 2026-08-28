import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import { cn } from '@/shared/lib/cn';

import { expectNoAxeViolations } from './axe.test-helpers';
import { SegmentedControl, type SegmentedOption } from './SegmentedControl';

const OPTIONS: SegmentedOption<'pool' | 'manual' | 'off'>[] = [
  { value: 'pool', label: 'Из пула' },
  { value: 'manual', label: 'Вручную' },
  { value: 'off', label: 'Без прокси' },
];

function renderControl(over: Partial<Parameters<typeof SegmentedControl>[0]> = {}) {
  const onChange = vi.fn();
  const { container } = render(
    <SegmentedControl
      value="pool"
      onChange={onChange}
      options={OPTIONS}
      ariaLabel="Прокси"
      {...over}
    />,
  );
  return { onChange, container, radios: screen.getAllByRole('radio') };
}

test('renders one radiogroup with one radio per option and exactly one checked', async () => {
  const { container, radios } = renderControl();
  expect(screen.getByRole('radiogroup', { name: 'Прокси' })).toBeInTheDocument();
  expect(radios).toHaveLength(3);
  expect(radios.map((r) => r.getAttribute('aria-checked'))).toEqual(['true', 'false', 'false']);
  await expectNoAxeViolations(container);
});

test('clicking an option reports its value', async () => {
  const { onChange } = renderControl();
  await userEvent.click(screen.getByRole('radio', { name: 'Вручную' }));
  expect(onChange).toHaveBeenCalledWith('manual');
});

/* ── the keyboard: one tab stop, arrows inside ───────────────────────────── */

test('the group is ONE tab stop, sitting on the checked option', () => {
  const { radios } = renderControl({ value: 'manual' });
  expect(radios.map((r) => r.tabIndex)).toEqual([-1, 0, -1]);
});

test('Tab enters the group once and Tab again leaves it', async () => {
  render(
    <>
      <SegmentedControl value="pool" onChange={vi.fn()} options={OPTIONS} ariaLabel="Прокси" />
      <button type="button">после</button>
    </>,
  );
  await userEvent.tab();
  expect(screen.getByRole('radio', { name: 'Из пула' })).toHaveFocus();
  await userEvent.tab();
  expect(screen.getByRole('button', { name: 'после' })).toHaveFocus();
});

test('ArrowRight moves to the next option, selects it and takes focus with it', async () => {
  const { onChange } = renderControl();
  await userEvent.tab();
  await userEvent.keyboard('{ArrowRight}');
  expect(onChange).toHaveBeenCalledWith('manual');
  expect(screen.getByRole('radio', { name: 'Вручную' })).toHaveFocus();
});

test('ArrowDown is ArrowRight and ArrowUp is ArrowLeft', async () => {
  const { onChange } = renderControl({ value: 'manual' });
  await userEvent.tab();
  await userEvent.keyboard('{ArrowDown}');
  expect(onChange).toHaveBeenLastCalledWith('off');
  await userEvent.keyboard('{ArrowUp}');
  expect(onChange).toHaveBeenLastCalledWith('pool');
});

test('the arrows wrap at both ends', async () => {
  const { onChange } = renderControl();
  await userEvent.tab();
  await userEvent.keyboard('{ArrowLeft}');
  expect(onChange).toHaveBeenLastCalledWith('off');
});

test('Home and End jump to the first and last option', async () => {
  const { onChange } = renderControl({ value: 'manual' });
  await userEvent.tab();
  await userEvent.keyboard('{End}');
  expect(onChange).toHaveBeenLastCalledWith('off');
  await userEvent.keyboard('{Home}');
  expect(onChange).toHaveBeenLastCalledWith('pool');
});

test('a key the control does not own is left to the page', async () => {
  const { onChange } = renderControl();
  await userEvent.tab();
  await userEvent.keyboard('{Escape}');
  expect(onChange).not.toHaveBeenCalled();
});

// A value that matches no option is a real state: Telegram reports privacy rules this
// app does not model, and the row must press nothing — while still being reachable.
test('a value outside the option list checks nothing and keeps the first tab stop', () => {
  const { radios } = renderControl({ value: 'unknown' });
  expect(radios.every((r) => r.getAttribute('aria-checked') === 'false')).toBe(true);
  expect(radios.map((r) => r.tabIndex)).toEqual([0, -1, -1]);
});

/* ── disabled ────────────────────────────────────────────────────────────── */

test('a disabled group takes neither clicks nor arrows', async () => {
  const { onChange, radios } = renderControl({ disabled: true });
  expect(radios.every((r) => (r as HTMLButtonElement).disabled)).toBe(true);
  await userEvent.click(radios[1] as HTMLElement);
  radios[0]?.focus();
  await userEvent.keyboard('{ArrowRight}');
  expect(onChange).not.toHaveBeenCalled();
});

/* ── the three fills ─────────────────────────────────────────────────────── */

test.each([
  ['tray', 'shadow-seg', 'text-content-muted'],
  ['pill', 'shadow-pill', 'text-content-muted'],
  ['outline', 'bg-info-tint', 'bg-surface-card'],
] as const)('the %s variant fills the checked option only', (variant, on, off) => {
  const { radios } = renderControl({ variant });
  expect(radios[0]).toHaveClass(on);
  expect(radios[0]).not.toHaveClass(off);
  expect(radios[1]).toHaveClass(off);
  expect(radios[1]).not.toHaveClass(on);
});

test('every option carries the focus ring the hand-written versions had none of', () => {
  const { radios } = renderControl();
  for (const radio of radios) {
    expect(radio).toHaveClass('focus-visible:outline-action-primary');
    // The glow this replaced measured 1.18:1, and it came with `outline-none`. On a
    // control that is one tab stop with an arrow-key cursor, an invisible focus ring
    // does not degrade the keyboard contract — it removes it.
    expect(radio.className).not.toContain('shadow-focus');
    expect(radio.className).not.toContain('outline-none');
  }
});

// cn() runs tailwind-merge, and tailwind-merge only keeps a class whose group it
// recognises. `shadow-seg`/`shadow-pill` and `rounded-sm`/`rounded-lg` are this
// config's own names; if a future class group is added without teaching cn.ts, the
// paint is dropped silently rather than erroring. Assert the merge keeps both halves.
test('cn keeps the size and the fill of a segment together', () => {
  expect(
    cn(
      'flex-1 rounded-sm py-sm text-body font-medium',
      'bg-surface-card text-content-primary shadow-seg',
    ),
  ).toBe(
    'flex-1 rounded-sm py-sm text-body font-medium bg-surface-card text-content-primary shadow-seg',
  );
  expect(
    cn('rounded-full px-lg py-tight text-body', 'bg-action-primary text-on-action shadow-pill'),
  ).toBe('rounded-full px-lg py-tight text-body bg-action-primary text-on-action shadow-pill');
});

/* ── per-option escape hatches ───────────────────────────────────────────── */

test('an option can carry its own accessible name and native tooltip', () => {
  renderControl({
    options: [
      { value: 'pool', label: 'Все', ariaLabel: 'Звонки: Все', title: 'подсказка' },
      { value: 'manual', label: 'Никто' },
    ],
  });
  expect(screen.getByRole('radio', { name: 'Звонки: Все' })).toHaveAttribute('title', 'подсказка');
});

test('a label may be rich content, not just a string', () => {
  renderControl({
    options: [
      {
        value: 'pool',
        label: (
          <>
            <div>Спокойный</div>
            <div>реже пишет</div>
          </>
        ),
      },
      { value: 'manual', label: 'Обычный' },
    ],
  });
  expect(screen.getByText('реже пишет')).toBeInTheDocument();
});

test('the wrapper takes the caller className without losing its own', () => {
  renderControl({ className: 'mb-lg' });
  const group = screen.getByRole('radiogroup');
  expect(group).toHaveClass('mb-lg');
  expect(group).toHaveClass('bg-canvas');
});
