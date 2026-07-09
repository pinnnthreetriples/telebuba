import { render, screen } from '@testing-library/react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { CaptchaSolverCard } from './CaptchaSolverCard';

test('the help tooltip uses the wide, wrapping popover so its text is not clipped', () => {
  render(
    <CaptchaSolverCard
      solverEnabled
      campaignId="c1"
      onToggleSolver={vi.fn()}
      captchaQueue={[]}
      accountLabel={(id) => id}
      onSolve={vi.fn()}
    />,
  );
  const tip = screen.getByText(/Движок сам решает бот-чек/);
  expect(tip).toHaveClass('tb-tip-pop');
  expect(tip).toHaveClass('tb-tip-pop--wide');
  // The --wide variant is center-aligned; the multi-sentence help text stays left.
  expect(tip).toHaveStyle({ textAlign: 'left' });
});
