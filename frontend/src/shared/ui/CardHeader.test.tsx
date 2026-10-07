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
