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

test('all tones have a hover treatment and retain the shared press state', () => {
  const { rerender } = render(
    <IconButton aria-label="a">
      <svg />
    </IconButton>,
  );
  const neutral = screen.getByRole('button', { name: 'a' });
  expect(neutral).toHaveClass(
    'hover:border-line-strong',
    'hover:bg-canvas',
    'hover:text-content-primary',
    'active:scale-press',
    'disabled:active:scale-rest',
    'motion-reduce:active:scale-rest',
  );

  rerender(
    <IconButton aria-label="a" tone="primary">
      <svg />
    </IconButton>,
  );
  expect(screen.getByRole('button', { name: 'a' })).toHaveClass(
    'hover:border-info-line',
    'hover:bg-action-hover',
    'hover:text-info-strong',
    'active:scale-press',
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
  expect(button).toHaveClass(
    'disabled:opacity-50',
    'disabled:pointer-events-none',
    'focus-visible:outline-focus',
    'active:scale-press',
  );
  await userEvent.click(button);
  expect(onClick).not.toHaveBeenCalled();
});

test('aria-busy icon actions cannot show press feedback', () => {
  render(
    <IconButton aria-label="Обновить" aria-busy="true">
      <svg />
    </IconButton>,
  );

  expect(screen.getByRole('button', { name: 'Обновить' })).toHaveClass(
    'aria-busy:pointer-events-none',
    'aria-busy:active:scale-rest',
  );
});

test('extra classes are appended, so a caller can size the glyph it puts inside', () => {
  render(
    <IconButton aria-label="Закрыть" className="text-title">
      ×
    </IconButton>,
  );

  expect(screen.getByRole('button', { name: 'Закрыть' })).toHaveClass('text-title', 'size-icon');
});
