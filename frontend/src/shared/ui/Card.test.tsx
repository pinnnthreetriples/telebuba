import { render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';

import { expectNoAxeViolations } from './axe.test-helpers';
import { Card } from './Card';

test('the surface is the card and the padding is the caller`s', () => {
  render(
    <Card data-testid="plain" className="p-lg">
      тело
    </Card>,
  );

  const card = screen.getByTestId('plain');
  expect(card.className).toContain('rounded-card');
  expect(card.className).toContain('border-line');
  expect(card.className).toContain('bg-surface-card');
  expect(card.className).toContain('p-lg');
  expect(card.className).not.toContain('px-xl');
});

test('the title and subtitle slots render only when given', async () => {
  const { container, rerender } = render(<Card data-testid="c">тело</Card>);
  expect(screen.getByTestId('c').textContent).toBe('тело');

  rerender(
    <Card data-testid="c" title="Ключи" subtitle="откуда берутся">
      тело
    </Card>,
  );
  expect(screen.getByText('Ключи')).toBeInTheDocument();
  expect(screen.getByText('откуда берутся')).toBeInTheDocument();
  await expectNoAxeViolations(container);
});

// A plain surface has no bottom margin: the pages that stack cards without a flex
// gap ask for one, and the twelve that were divs before this did not.
test('the bottom margin is opt-in', () => {
  const { rerender } = render(<Card data-testid="c">тело</Card>);
  expect(screen.getByTestId('c').className).not.toContain('mb-');

  rerender(
    <Card data-testid="c" mb="mb-lg">
      тело
    </Card>,
  );
  expect(screen.getByTestId('c').className).toContain('mb-lg');
});
