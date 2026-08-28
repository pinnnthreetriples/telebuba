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

test('size picks both the box and its shape, so `tile` is the only circle', () => {
  const { rerender } = render(
    <IconButton aria-label="a" size="sm">
      <svg />
    </IconButton>,
  );
  expect(screen.getByRole('button', { name: 'a' })).toHaveClass('size-chip', 'rounded-sm');

  rerender(
    <IconButton aria-label="a" size="md">
      <svg />
    </IconButton>,
  );
  expect(screen.getByRole('button', { name: 'a' })).toHaveClass('size-icon', 'rounded-md');

  rerender(
    <IconButton aria-label="a" size="lg">
      <svg />
    </IconButton>,
  );
  expect(screen.getByRole('button', { name: 'a' })).toHaveClass('size-tile', 'rounded-full');

  rerender(
    <IconButton aria-label="a" size="touch">
      <svg />
    </IconButton>,
  );
  expect(screen.getByRole('button', { name: 'a' })).toHaveClass('size-touch', 'rounded-md');
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
