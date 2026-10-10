import { render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';

import { expectNoAxeViolations } from './axe.test-helpers';
import { ProgressBar } from './ProgressBar';

function fillWidth(container: HTMLElement): string | undefined {
  return (container.firstElementChild?.firstElementChild as HTMLElement | null)?.style.width;
}

test('a labelled bar is a progressbar with its range', async () => {
  const { container } = render(<ProgressBar label="Прогресс" value={3} max={4} />);
  const bar = screen.getByRole('progressbar', { name: 'Прогресс' });
  expect(bar).toHaveAttribute('aria-valuemin', '0');
  expect(bar).toHaveAttribute('aria-valuemax', '4');
  expect(bar).toHaveAttribute('aria-valuenow', '3');
  expect(fillWidth(container)).toBe('75%');
  await expectNoAxeViolations(container);
});

test('an unlabelled bar claims no role: capacity and scores are not progress', () => {
  render(<ProgressBar value={40} />);
  expect(screen.queryByRole('progressbar')).toBeNull();
});

test('the fill never leaves the track, and an empty range is an empty bar', () => {
  const { container: over } = render(<ProgressBar value={9} max={4} />);
  expect(fillWidth(over)).toBe('100%');
  const { container: none } = render(<ProgressBar value={3} max={0} />);
  expect(fillWidth(none)).toBe('0%');
});

test('indeterminate: the track breathes, there is no fill and no value', () => {
  const { container } = render(<ProgressBar label="Поиск" value={0} max={0} indeterminate />);
  const bar = screen.getByRole('progressbar', { name: 'Поиск' });
  expect(bar.className).toContain('tb-pulse');
  expect(bar).not.toHaveAttribute('aria-valuenow');
  expect(container.firstElementChild?.childElementCount).toBe(0);
});

test('the tone paints the fill; `current` takes the caller`s ink', () => {
  const { container } = render(<ProgressBar value={50} tone="current" className="text-danger" />);
  expect(container.firstElementChild?.className).toContain('text-danger');
  expect(container.firstElementChild?.firstElementChild?.className).toContain('bg-current');
});

test('segmented: days done are green, the day in progress blue, the rest grey', () => {
  const { container } = render(<ProgressBar segments={10} value={3} max={10} />);
  const cells = [...(container.firstElementChild?.children ?? [])].map((cell) => cell.className);
  expect(cells).toHaveLength(10);
  expect(cells.slice(0, 3).every((cell) => cell.includes('bg-success'))).toBe(true);
  expect(cells[3]).toContain('bg-action-primary');
  expect(cells.slice(4).every((cell) => cell.includes('bg-line'))).toBe(true);
  // The slit between days is a transparent border the fill stops short of.
  expect(cells[0]).toContain('bg-clip-padding');
});

test('a finished segmented bar has no day in progress', () => {
  const { container } = render(<ProgressBar segments={4} value={14} max={14} />);
  const cells = [...(container.firstElementChild?.children ?? [])];
  expect(cells.every((cell) => cell.className.includes('bg-success'))).toBe(true);
});
