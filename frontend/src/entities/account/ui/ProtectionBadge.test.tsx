import { render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';

import '@/shared/i18n';

import { ProtectionBadge } from './ProtectionBadge';

test('shows the score and lists what is missing in the tooltip', () => {
  render(<ProtectionBadge protection={{ score: 5, total: 7, missing: ['passkey', 'proxy'] }} />);
  const chip = screen.getByTestId('account-protection');
  expect(chip).toHaveTextContent('Защита 5/7');
  expect(chip.title).toContain('Нет passkey');
  expect(chip.title).toContain('Не привязан прокси');
  expect(chip.className).toContain('warning');
});

test('a full score has no tooltip and the ok tone', () => {
  render(<ProtectionBadge protection={{ score: 7, total: 7, missing: [] }} />);
  const chip = screen.getByTestId('account-protection');
  expect(chip).toHaveTextContent('Защита 7/7');
  expect(chip).not.toHaveAttribute('title');
  expect(chip.className).toContain('success');
});

test('four or fewer is the danger tone', () => {
  render(<ProtectionBadge protection={{ score: 4, total: 7, missing: ['proxy'] }} />);
  expect(screen.getByTestId('account-protection').className).toContain('danger');
});
