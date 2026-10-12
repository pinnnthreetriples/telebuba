import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { needsGeoConsent, regionName } from '../lib/geo';
import { countdown } from '../lib/countdown';
import { GeoMismatchBanner } from './GeoMismatchBanner';
import { HandoffModal } from './HandoffModal';

test('the soft start is preselected and quotes the served numbers', async () => {
  const onConfirm = vi.fn();
  const onClose = vi.fn();
  render(
    <HandoffModal
      plan={{ days: 4, early_days: 1, first_day_comments: 3, last_day_comments: 9 }}
      onClose={onClose}
      onConfirm={onConfirm}
    />,
  );

  const soft = screen.getByRole('radio', { name: /Мягкий старт \(рекомендуем\)/ });
  expect(soft).toHaveAttribute('aria-checked', 'true');
  expect(soft).toHaveTextContent('4 дн. сниженных лимитов: дни 1–1');
  expect(soft).toHaveTextContent('дни 2–4');
  expect(soft).toHaveTextContent('Комментариев в день: 3 → 9.');

  await userEvent.click(screen.getByRole('button', { name: 'Передать' }));
  expect(onConfirm).toHaveBeenCalledWith('soft');
  expect(onClose).toHaveBeenCalled();
});

test('«Как обычно» hands off with the usual limits', async () => {
  const onConfirm = vi.fn();
  render(<HandoffModal onClose={vi.fn()} onConfirm={onConfirm} />);

  await userEvent.click(screen.getByRole('radio', { name: /Как обычно/ }));
  expect(screen.getByRole('radio', { name: /Как обычно/ })).toHaveAttribute('aria-checked', 'true');
  await userEvent.click(screen.getByRole('button', { name: 'Передать' }));

  expect(onConfirm).toHaveBeenCalledWith('normal');
});

test('cancel hands nothing off', async () => {
  const onConfirm = vi.fn();
  render(<HandoffModal onClose={vi.fn()} onConfirm={onConfirm} />);

  await userEvent.click(screen.getByRole('button', { name: 'Отмена' }));

  expect(onConfirm).not.toHaveBeenCalled();
});

test('the geo banner counts unaccepted mismatches and hides at zero', () => {
  const { rerender } = render(<GeoMismatchBanner count={3} />);
  expect(screen.getByText('У 3 аккаунтов гео номера и прокси не совпадает')).toBeInTheDocument();

  rerender(<GeoMismatchBanner count={0} />);
  expect(screen.queryByRole('status')).not.toBeInTheDocument();
});

test('only an unaccepted mismatch needs a consent', () => {
  expect(needsGeoConsent({ geo_status: 'mismatch' })).toBe(true);
  expect(needsGeoConsent({ geo_status: 'mismatch', geo_accepted: true })).toBe(false);
  expect(needsGeoConsent({ geo_status: 'match' })).toBe(false);
  expect(needsGeoConsent({})).toBe(false);
});

test('countries are named in the UI language, the code when unknown', () => {
  const usInRussian = new Intl.DisplayNames(['ru'], { type: 'region' }).of('US');
  expect(regionName('us', 'ru')).toBe(usInRussian);
  expect(regionName(null, 'ru')).toBe('—');
  expect(regionName('US', 'not a locale!')).toBe('US');
});

test('the countdown reads hours and minutes, nothing once passed', () => {
  const now = Date.parse('2026-06-13T12:00:00Z');
  expect(countdown('2026-06-13T13:20:00Z', now)).toBe('1:20');
  expect(countdown('2026-06-13T12:00:30Z', now)).toBe('0:01');
  expect(countdown('2026-06-13T11:00:00Z', now)).toBeNull();
  expect(countdown('garbage', now)).toBeNull();
  expect(countdown(null, now)).toBeNull();
});
