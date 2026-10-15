import { render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';

import '@/shared/i18n';

import { SurvivalBadge } from './SurvivalBadge';

test('the badge says the day after release and the status', () => {
  render(<SurvivalBadge survival={{ days_since_release: 12, status: 'alive' }} />);

  const badge = screen.getByText('День 12 после выпуска · жив');
  expect(badge.className).toContain('success');
});

test('a dead account wears the danger tone', () => {
  render(
    <SurvivalBadge survival={{ days_since_release: 3, status: 'dead', cause: 'session_dead' }} />,
  );

  expect(screen.getByText('День 3 после выпуска · умер').className).toContain('danger');
});
