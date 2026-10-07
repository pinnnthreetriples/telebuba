import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import { expectNoAxeViolations } from './axe.test-helpers';
import { CardHeader } from './CardHeader';

test('title, badge, subtitle and the right-hand slot', async () => {
  const { container } = render(
    <CardHeader
      title="Доска работ"
      badge={<span>4</span>}
      subtitle="обновляется сама"
      icon={<svg aria-hidden="true" />}
    >
      <button type="button">Настроить</button>
    </CardHeader>,
  );
  expect(screen.getByText('Доска работ').className).toContain('type-h3');
  expect(screen.getByText('Доска работ').nextElementSibling).toHaveTextContent('4');
  expect(screen.getByText('обновляется сама').className).toContain('type-small');
  const row = container.firstElementChild as HTMLElement;
  expect(row.lastElementChild).toHaveTextContent('Настроить');
  await expectNoAxeViolations(container);
});

test('the icon sits on a tile of the chosen tone', () => {
  const { container } = render(
    <CardHeader title="Прогреты" tone="success" icon={<svg data-testid="i" />} />,
  );
  const tile = screen.getByTestId('i').parentElement as HTMLElement;
  expect(tile.className).toContain('bg-success-tint');
  expect(tile.className).toContain('size-icon');
  expect(container.querySelector('.size-dot')).toBeNull();
});

test('the status dot says live, idle or active', () => {
  const { container, rerender } = render(<CardHeader title="Лента" dot="live" />);
  expect(container.querySelector('.size-dot')?.className).toContain('tb-livedot');
  rerender(<CardHeader title="Лента" dot="idle" />);
  expect(container.querySelector('.size-dot')?.className).toContain('bg-content-subtle');
  rerender(<CardHeader title="Лента" dot="active" />);
  expect(container.querySelector('.size-dot')?.className).toContain('pl-pulse');
});

test('a wrapping header lets the right-hand slot drop below instead of squeezing the title', () => {
  const { container, rerender } = render(
    <CardHeader title="Конвейер">
      <button type="button">Запустить</button>
    </CardHeader>,
  );
  const row = container.firstElementChild as HTMLElement;
  expect(row.className).not.toContain('flex-wrap');
  expect((row.firstElementChild as HTMLElement).className).toContain('flex-1');

  rerender(
    <CardHeader wrap title="Конвейер">
      <button type="button">Запустить</button>
    </CardHeader>,
  );
  expect(row.className).toContain('flex-wrap');
  expect((row.firstElementChild as HTMLElement).className).toContain('flex-auto');
});

test('with `toggle` the heading is the disclosure button', async () => {
  const onToggle = vi.fn();
  render(
    <>
      <CardHeader title="Каналы" toggle={{ expanded: false, controls: 'body', onToggle }} />
      <div id="body" />
    </>,
  );
  const button = screen.getByRole('button', { name: 'Каналы' });
  expect(button).toHaveAttribute('aria-expanded', 'false');
  expect(button).toHaveAttribute('aria-controls', 'body');
  await userEvent.click(button);
  expect(onToggle).toHaveBeenCalledTimes(1);
});

test('`aside` sits right after the title, outside the toggle, and the right slot stays right', async () => {
  const { container } = render(
    <>
      <CardHeader
        title="Доска работ"
        toggle={{ expanded: true, controls: 'body', onToggle: vi.fn() }}
        aside={<button type="button">В работе</button>}
      >
        <button type="button">Настроить</button>
      </CardHeader>
      <div id="body" />
    </>,
  );
  const toggle = screen.getByRole('button', { name: 'Доска работ' });
  const aside = screen.getByRole('button', { name: 'В работе' });
  expect(toggle.className).not.toContain('flex-1');
  expect(toggle.nextElementSibling).toBe(aside);
  const left = toggle.parentElement as HTMLElement;
  expect(left.className).toContain('flex-1');
  expect(left.nextElementSibling).toHaveTextContent('Настроить');
  await expectNoAxeViolations(container);
});

test('with `aside` the empty run of the header still folds the card, without a second control', async () => {
  const onToggle = vi.fn();
  const { container } = render(
    <>
      <CardHeader
        title="Доска работ"
        toggle={{ expanded: true, controls: 'body', onToggle }}
        aside={<button type="button">В работе</button>}
      />
      <div id="body" />
    </>,
  );
  const aside = screen.getByRole('button', { name: 'В работе' });
  const spacer = aside.nextElementSibling as HTMLElement;
  expect(spacer.className).toContain('flex-1');
  expect(spacer).toHaveAttribute('aria-hidden', 'true');
  expect(spacer).toHaveAttribute('tabindex', '-1');
  // The title button stays the one named control that toggles.
  expect(screen.getAllByRole('button').map((button) => button.textContent)).toEqual([
    'Доска работ',
    'В работе',
  ]);
  await userEvent.click(spacer);
  expect(onToggle).toHaveBeenCalledTimes(1);
  await expectNoAxeViolations(container);
});

test('without `aside` or without `toggle` there is no spacer', () => {
  const { container, rerender } = render(
    <CardHeader title="Каналы" toggle={{ expanded: false, controls: 'b', onToggle: vi.fn() }} />,
  );
  expect(container.querySelectorAll('button')).toHaveLength(1);
  rerender(<CardHeader title="Каналы" aside={<span>x</span>} />);
  expect(container.querySelectorAll('button')).toHaveLength(0);
});
