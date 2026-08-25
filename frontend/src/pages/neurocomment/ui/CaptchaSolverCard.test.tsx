import { render, screen } from '@testing-library/react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { CaptchaSolverCard } from './CaptchaSolverCard';

function renderCard() {
  render(
    <CaptchaSolverCard
      solverEnabled
      campaignId="c1"
      onToggleSolver={vi.fn()}
      captchaQueue={[]}
      accountLabel={(id) => id}
    />,
  );
}

test('the help tooltip uses the wide, wrapping popover so its text is not clipped', () => {
  renderCard();
  const tip = screen.getByText(/Движок сам решает бот-чек/);
  expect(tip).toHaveClass('tb-tip-pop');
  expect(tip).toHaveClass('tb-tip-pop--wide');
  // The --wide variant is center-aligned; the multi-sentence help text stays left.
  expect(tip).toHaveStyle({ textAlign: 'left' });
});

// `.tb-tip-pop` opens on `:hover` and `:focus-within` (asserted on the stylesheet in
// src/app/styles/index.test.ts — happy-dom applies no CSS, so the reveal itself cannot be
// seen from here). This is the component's half: a badge nothing can focus makes the
// second selector unreachable, and a bubble nothing points at is invisible to a screen
// reader whether it is revealed or not.
test('the help badge is reachable by keyboard and names its tooltip', () => {
  renderCard();
  const badge = screen.getByText('?');

  badge.focus();
  expect(badge).toHaveFocus();

  const describedBy = badge.getAttribute('aria-describedby');
  expect(describedBy).not.toBeNull();
  const tip = document.getElementById(describedBy ?? '');
  expect(tip).toHaveAttribute('role', 'tooltip');
  expect(tip).toHaveTextContent(/Движок сам решает бот-чек/);
});
