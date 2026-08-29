import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import { expectNoAxeViolations } from './axe.test-helpers';
import { IconButton } from './IconButton';

test('is reachable by its accessible name and defaults to a non-submitting button', async () => {
  const onClick = vi.fn();
  const { container } = render(
    <form
      onSubmit={() => {
        throw new Error('an icon button must not submit its form');
      }}
    >
      <IconButton aria-label="Удалить" onClick={onClick}>
        <svg />
      </IconButton>
    </form>,
  );

  const button = screen.getByRole('button', { name: 'Удалить' });
  expect(button).toHaveAttribute('type', 'button');
  await userEvent.click(button);
  expect(onClick).toHaveBeenCalledTimes(1);
  await expectNoAxeViolations(container);
});

// Ступень задаёт КОРОБКУ, и только её. Форма раньше приходила из той же таблицы, с
// объяснением на каждую ступень: `sm` — `rounded-sm`, `lg` — круг, `touch` — обратно
// квадрат. Три объяснения на четыре ступени означали, что сменить размер иконочной кнопки
// нельзя, не сменив её форму. Тест перебирает ВСЕ ступени, а не две: утверждение здесь —
// «радиус один», и проверить его можно только на полном наборе.
test('ступень задаёт коробку, а радиус у всех ступеней один', () => {
  const boxes = { sm: 'size-chip', md: 'size-icon', lg: 'size-tile', touch: 'size-touch' } as const;

  for (const [size, box] of Object.entries(boxes) as [keyof typeof boxes, string][]) {
    const { unmount } = render(
      <IconButton aria-label="a" size={size}>
        <svg />
      </IconButton>,
    );
    expect(screen.getByRole('button', { name: 'a' })).toHaveClass(box, 'rounded-md');
    unmount();
  }
});

// Круг — запрос, а не побочный эффект ступени.
test('круг приходит только из shape', () => {
  const { rerender } = render(
    <IconButton aria-label="a" size="lg">
      <svg />
    </IconButton>,
  );
  expect(screen.getByRole('button', { name: 'a' })).not.toHaveClass('rounded-full');

  rerender(
    <IconButton aria-label="a" size="lg" shape="circle">
      <svg />
    </IconButton>,
  );
  expect(screen.getByRole('button', { name: 'a' })).toHaveClass('rounded-full');
});

test('tone paints the hover, and neutral deliberately has none', () => {
  const { rerender } = render(
    <IconButton aria-label="a">
      <svg />
    </IconButton>,
  );
  expect(screen.getByRole('button', { name: 'a' }).className).not.toMatch(/hover:/);

  rerender(
    <IconButton aria-label="a" tone="primary">
      <svg />
    </IconButton>,
  );
  expect(screen.getByRole('button', { name: 'a' })).toHaveClass(
    'hover:border-info-line',
    'hover:bg-action-hover',
    'hover:text-info-strong',
  );

  rerender(
    <IconButton aria-label="a" tone="danger">
      <svg />
    </IconButton>,
  );
  expect(screen.getByRole('button', { name: 'a' })).toHaveClass(
    'hover:border-danger-line',
    'hover:bg-danger-tint',
    'hover:text-danger-deep',
  );
});

test('disabled is inert and dimmed, so a pending action cannot be fired twice', async () => {
  const onClick = vi.fn();
  render(
    <IconButton aria-label="Обновить" disabled onClick={onClick}>
      <svg />
    </IconButton>,
  );

  const button = screen.getByRole('button', { name: 'Обновить' });
  expect(button).toBeDisabled();
  expect(button).toHaveClass('disabled:opacity-50');
  await userEvent.click(button);
  expect(onClick).not.toHaveBeenCalled();
});

test('extra classes are appended, so a caller can size the glyph it puts inside', () => {
  render(
    <IconButton aria-label="Закрыть" className="text-title">
      ×
    </IconButton>,
  );

  expect(screen.getByRole('button', { name: 'Закрыть' })).toHaveClass('text-title', 'size-icon');
});
