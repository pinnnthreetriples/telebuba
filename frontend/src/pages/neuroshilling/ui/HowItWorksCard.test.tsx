import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test } from 'vitest';

import '@/shared/i18n';

import { HowItWorksCard } from './HowItWorksCard';

test('lists the four numbered steps once expanded', async () => {
  render(<HowItWorksCard />);
  await userEvent.click(screen.getByText('Как это работает'));

  for (const step of ['1', '2', '3', '4']) {
    expect(screen.getByText(step)).toBeInTheDocument();
  }
  expect(screen.getByText(/Создайте кампанию/)).toBeInTheDocument();
  expect(screen.getByText(/Запустите прогон/)).toBeInTheDocument();
});
