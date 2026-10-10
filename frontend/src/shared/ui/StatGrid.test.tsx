import { render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';

import { expectNoAxeViolations } from './axe.test-helpers';
import { StatGrid, StatTile } from './StatGrid';

test('a number rolls, a string stands — both read as the value', () => {
  render(
    <>
      <StatTile label="Аккаунтов" value={12} tone="success" />
      <StatTile label="Диалог" value="4:30" />
    </>,
  );
  const rolled = screen.getByText('Аккаунтов').previousElementSibling as HTMLElement;
  expect(rolled.querySelector('.sr-only')).toHaveTextContent('12');
  expect(rolled.className).toContain('text-success-deep');
  expect(screen.getByText('Диалог').previousElementSibling).toHaveTextContent('4:30');
});

test('the label sits one step under the number', () => {
  render(<StatTile label="Ошибок" value={0} />);
  expect(screen.getByText('Ошибок').className).toContain('mt-1');
});

test('up to three tiles stay in one row and divide with their own left border', async () => {
  const { container } = render(
    <StatGrid
      stats={[
        { label: 'В прогреве', value: 3 },
        { label: 'Готовы', value: 1 },
        { label: 'Ошибки', value: 0, tone: 'danger' },
      ]}
    />,
  );
  const grid = container.firstElementChild as HTMLElement;
  expect(grid.className).toContain('grid-cols-3');
  expect(grid.className).not.toContain('gap');
  expect((grid.children[1] as HTMLElement).className).toContain('border-l');
  await expectNoAxeViolations(container);
});

test('more than three pair up below md and line up from md', () => {
  const stats = ['a', 'b', 'c', 'd', 'e'].map((label) => ({ label, value: 1 }));
  const { container } = render(<StatGrid stats={stats} />);
  const grid = container.firstElementChild as HTMLElement;
  expect(grid.className).toContain('grid-cols-2');
  expect(grid.className).toContain('md:grid-cols-5');
  // An odd trailing tile spans both columns below md.
  expect((grid.children[4] as HTMLElement).className).toContain('max-md:odd:last:col-span-2');
});
