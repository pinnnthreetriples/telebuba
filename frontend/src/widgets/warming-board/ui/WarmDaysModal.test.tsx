import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { WarmDaysModal } from './WarmDaysModal';

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

function renderWithClient(ui: ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

test('presets, keyboard arrows and confirm', async () => {
  const onClose = vi.fn();
  const onConfirm = vi.fn();
  renderWithClient(
    <WarmDaysModal accountId="a1" phone="+79991234567" onClose={onClose} onConfirm={onConfirm} />,
  );
  expect(screen.getByText('Прогрев аккаунта')).toBeInTheDocument();

  const slider = screen.getByRole('slider');
  expect(slider).toHaveAttribute('aria-valuenow', '7');

  // preset button sets the day count
  await userEvent.click(screen.getByText('3 дня'));
  expect(slider).toHaveAttribute('aria-valuenow', '3');

  // keyboard arrows move within bounds
  slider.focus();
  await userEvent.keyboard('{ArrowRight}{ArrowRight}');
  expect(slider).toHaveAttribute('aria-valuenow', '5');
  await userEvent.keyboard('{ArrowLeft}');
  expect(slider).toHaveAttribute('aria-valuenow', '4');

  await userEvent.click(screen.getByText('Запустить прогрев'));
  // defaults to the balanced persona when none is picked
  expect(onConfirm).toHaveBeenCalledWith(4, 'normal');
  expect(onClose).toHaveBeenCalledTimes(1);
});

test('persona chip selection is forwarded on confirm', async () => {
  const onClose = vi.fn();
  const onConfirm = vi.fn();
  renderWithClient(
    <WarmDaysModal accountId="a1" phone="+79991234567" onClose={onClose} onConfirm={onConfirm} />,
  );

  await userEvent.click(screen.getByText('Активный'));
  await userEvent.click(screen.getByText('Запустить прогрев'));

  expect(onConfirm).toHaveBeenCalledWith(7, 'active');
});

test('cancel closes without confirming', async () => {
  const onClose = vi.fn();
  const onConfirm = vi.fn();
  renderWithClient(
    <WarmDaysModal accountId="a1" phone="+79991234567" onClose={onClose} onConfirm={onConfirm} />,
  );
  await userEvent.click(screen.getByText('Отмена'));
  expect(onClose).toHaveBeenCalledTimes(1);
  expect(onConfirm).not.toHaveBeenCalled();
});

// Both of this dialog's tooltips hang off `.tb-tip`, which opens on `:hover` and
// `:focus-within` (the stylesheet's half is asserted in src/app/styles/index.test.ts;
// happy-dom applies no CSS, so the reveal cannot be seen from here). Both triggers were
// already tab stops, so the missing piece was only the description: a `.tb-tip-pop` with
// no `role` and nothing pointing at it is a sibling <span> of text as far as a screen
// reader is concerned, revealed or not.
test('both tooltips are named by the control they explain', () => {
  renderWithClient(
    <WarmDaysModal accountId="a1" phone="+79991234567" onClose={vi.fn()} onConfirm={vi.fn()} />,
  );

  for (const [trigger, text] of [
    [screen.getByText('Спам-чек').closest('button'), /на @SpamBot/],
    [screen.getByRole('button', { name: 'Тип пользователя' }), /Как часто аккаунт заходит/],
  ] as const) {
    trigger?.focus();
    expect(trigger).toHaveFocus();
    const tip = document.getElementById(trigger?.getAttribute('aria-describedby') ?? '');
    expect(tip).toHaveAttribute('role', 'tooltip');
    expect(tip).toHaveTextContent(text);
  }
});

test('spam-check button runs the real @SpamBot probe and shows the verdict', async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    if (request.url.includes('/spam-check')) {
      return Promise.resolve(
        jsonResponse({ account_id: 'a1', status: 'clean', detail: null, checked_at: 'now' }),
      );
    }
    return Promise.resolve(jsonResponse({}));
  });

  renderWithClient(
    <WarmDaysModal accountId="a1" phone="+79991234567" onClose={vi.fn()} onConfirm={vi.fn()} />,
  );

  await userEvent.click(screen.getByText('Спам-чек'));
  // The probe hits the account's spam-check endpoint.
  await waitFor(() => {
    const called = vi
      .mocked(fetch)
      .mock.calls.some(([input]) => (input as Request).url.includes('/accounts/a1/spam-check'));
    expect(called).toBe(true);
  });
  // And the clean verdict is surfaced on the pill.
  await waitFor(() => {
    expect(screen.getByText('Чисто')).toBeInTheDocument();
  });
});
