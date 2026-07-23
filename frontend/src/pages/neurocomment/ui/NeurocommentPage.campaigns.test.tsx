import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { renderWithClient, routeApi } from './NeurocommentPage.testHelpers';
import { NeurocommentPage } from './NeurocommentPage';

test('the create-campaign button opens the create modal', async () => {
  routeApi();
  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getAllByText('@news').length).toBeGreaterThan(0);
  });
  await userEvent.click(screen.getByText('+ Создать кампанию'));
  expect(screen.getByText('Создать кампанию')).toBeInTheDocument();
});

test('selecting a campaign card marks it selected', async () => {
  routeApi();
  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getAllByText('Promo').length).toBeGreaterThan(0);
  });
  const card = screen
    .getAllByText('Promo')
    .map((node) => node.closest('[role="button"]'))
    .find((node): node is HTMLElement => node !== null);
  expect(card).toBeDefined();
  await userEvent.click(card as HTMLElement);
  expect((card as HTMLElement).className).toContain('border-primary');
  // sanity: status pill uses the active campaign-status key path
  within(card as HTMLElement).getByText('Активна');
});

test('campaign edit-prompt saves and delete removes the campaign', async () => {
  routeApi();
  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getAllByText('Promo').length).toBeGreaterThan(0);
  });

  await userEvent.click(screen.getByTitle('Редактировать промт'));
  // Bug fix: an unpinned account shows the CAMPAIGN scope in the modal, not an
  // arbitrary first-readiness channel (`@news`). The account subtitle is the only
  // muted-text 'Promo' on the page.
  expect(await screen.findByText('Promo', { selector: '.text-ink-muted' })).toBeInTheDocument();
  await userEvent.click(await screen.findByText('Сохранить'));
  await waitFor(() => {
    const saved = vi.mocked(fetch).mock.calls.some(([input]) => {
      const request = input as Request;
      return request.url.includes('/campaigns/c1/prompt') && request.method === 'PUT';
    });
    expect(saved).toBe(true);
  });

  await userEvent.click(screen.getByTitle('Удалить кампанию'));
  await userEvent.click(await screen.findByText('Удалить'));
  await waitFor(() => {
    const deleted = vi.mocked(fetch).mock.calls.some(([input]) => {
      const request = input as Request;
      return request.url.endsWith('/neurocomment/campaigns/c1') && request.method === 'DELETE';
    });
    expect(deleted).toBe(true);
  });
});

test('the create-campaign modal closes via cancel', async () => {
  routeApi();
  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getAllByText('@news').length).toBeGreaterThan(0);
  });
  await userEvent.click(screen.getByText('+ Создать кампанию'));
  expect(screen.getByText('Создать кампанию')).toBeInTheDocument();
  await userEvent.click(screen.getAllByText('Отмена')[0]!);
  await waitFor(() => {
    expect(screen.queryByText('Создать кампанию')).not.toBeInTheDocument();
  });
});

test('a campaign card shows its OWN channel/account counts, not the board totals', async () => {
  // Finding #3: counts come from the campaign payload (3 / 5), not the board.
  routeApi();
  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getAllByText('Promo').length).toBeGreaterThan(0);
  });
  expect(screen.getByText('3 каналов · 5 аккаунтов')).toBeInTheDocument();
});

test('per-campaign run/pause calls setCampaignStatus, not the global stop', async () => {
  // Finding #2: an active campaign's pause button flips its status via the
  // status endpoint; it must NOT hit /neurocomment/stop.
  routeApi();
  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getAllByText('Promo').length).toBeGreaterThan(0);
  });
  // The active campaign card exposes a "pause" action (title from campaign.status).
  await userEvent.click(screen.getAllByTitle('Поставить на паузу')[0]!);
  await waitFor(() => {
    const setStatus = vi.mocked(fetch).mock.calls.some(([input]) => {
      const request = input as Request;
      return request.url.endsWith('/campaigns/c1/status') && request.method === 'POST';
    });
    expect(setStatus).toBe(true);
  });
  const stopped = vi
    .mocked(fetch)
    .mock.calls.some(([input]) => (input as Request).url.endsWith('/neurocomment/stop'));
  expect(stopped).toBe(false);
});

test('the campaign gear toggles the slide-out actions', async () => {
  routeApi();
  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getAllByText('Promo').length).toBeGreaterThan(0);
  });
  const gear = screen.getAllByLabelText('Действия')[0]!;
  expect(gear).toHaveAttribute('aria-expanded', 'false');
  await userEvent.click(gear);
  expect(gear).toHaveAttribute('aria-expanded', 'true');
});
