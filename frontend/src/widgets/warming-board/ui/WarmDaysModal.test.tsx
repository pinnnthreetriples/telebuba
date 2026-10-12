import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen, waitFor } from '@testing-library/react';
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
    <WarmDaysModal
      accountId="a1"
      phone="+79991234567"
      defaultDays={14}
      onClose={onClose}
      onConfirm={onConfirm}
    />,
  );
  expect(screen.getByText('Прогрев аккаунта')).toBeInTheDocument();

  const slider = screen.getByRole('slider', { name: 'Прогрев аккаунта' });
  expect(slider).toHaveAttribute('aria-valuenow', '14');

  // preset button sets the day count
  await userEvent.click(screen.getByRole('radio', { name: '7 дней' }));
  expect(slider).toHaveAttribute('aria-valuenow', '7');

  // keyboard arrows move within bounds
  slider.focus();
  await userEvent.keyboard('{ArrowRight}{ArrowRight}');
  expect(slider).toHaveAttribute('aria-valuenow', '9');
  await userEvent.keyboard('{ArrowLeft}');
  expect(slider).toHaveAttribute('aria-valuenow', '8');

  await userEvent.click(screen.getByText('Запустить прогрев'));
  // defaults to the balanced persona when none is picked
  expect(onConfirm).toHaveBeenCalledWith(8, 'normal', 3);
  expect(onClose).toHaveBeenCalledTimes(1);
});

test('opens on the board default and reaches thirty days, no further', async () => {
  const onConfirm = vi.fn();
  renderWithClient(
    <WarmDaysModal
      accountId="a1"
      phone="+79991234567"
      defaultDays={21}
      onClose={vi.fn()}
      onConfirm={onConfirm}
    />,
  );
  const slider = screen.getByRole('slider', { name: 'Прогрев аккаунта' });
  expect(slider).toHaveAttribute('aria-valuenow', '21');
  expect(slider).toHaveAttribute('aria-valuemax', '30');

  await userEvent.click(screen.getByRole('radio', { name: '30 дней' }));
  slider.focus();
  await userEvent.keyboard('{ArrowRight}');
  expect(slider).toHaveAttribute('aria-valuenow', '30');
  expect(slider).toHaveAttribute('aria-valuetext', '30 дней');
  // Home/End jump to the ends, as the slider pattern expects.
  await userEvent.keyboard('{Home}');
  expect(slider).toHaveAttribute('aria-valuenow', '1');
  await userEvent.keyboard('{End}');
  expect(slider).toHaveAttribute('aria-valuenow', '30');

  await userEvent.click(screen.getByText('Запустить прогрев'));
  expect(onConfirm).toHaveBeenCalledWith(30, 'normal', 3);
});

test('a configured default past thirty days widens the slider instead of being cut', async () => {
  const onConfirm = vi.fn();
  renderWithClient(
    <WarmDaysModal
      accountId="a1"
      phone="+79991234567"
      defaultDays={45}
      onClose={vi.fn()}
      onConfirm={onConfirm}
    />,
  );
  const slider = screen.getByRole('slider', { name: 'Прогрев аккаунта' });
  expect(slider).toHaveAttribute('aria-valuenow', '45');
  expect(slider).toHaveAttribute('aria-valuemax', '45');

  await userEvent.click(screen.getByText('Запустить прогрев'));
  expect(onConfirm).toHaveBeenCalledWith(45, 'normal', 3);
});

test('handled slider keys do not also scroll the dialog', () => {
  renderWithClient(
    <WarmDaysModal accountId="a1" phone="+79991234567" onClose={vi.fn()} onConfirm={vi.fn()} />,
  );
  const slider = screen.getByRole('slider', { name: 'Прогрев аккаунта' });
  for (const key of ['Home', 'End', 'ArrowLeft', 'ArrowRight']) {
    const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true });
    act(() => {
      slider.dispatchEvent(event);
    });
    expect(event.defaultPrevented).toBe(true);
  }
  // An unhandled key keeps its browser behaviour (Tab still leaves the slider).
  const tab = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true });
  slider.dispatchEvent(tab);
  expect(tab.defaultPrevented).toBe(false);
});

test('persona chip selection is forwarded on confirm', async () => {
  const onClose = vi.fn();
  const onConfirm = vi.fn();
  renderWithClient(
    <WarmDaysModal accountId="a1" phone="+79991234567" onClose={onClose} onConfirm={onConfirm} />,
  );

  await userEvent.click(screen.getByText('Активный'));
  await userEvent.click(screen.getByText('Запустить прогрев'));

  // Without a board default the dialog opens on the two-week stint.
  expect(onConfirm).toHaveBeenCalledWith(14, 'active', 3);
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

test('a picked length asks before Escape closes; an untouched dialog closes at once', async () => {
  const onClose = vi.fn();
  const { unmount } = renderWithClient(
    <WarmDaysModal accountId="a1" phone="+79991234567" onClose={onClose} onConfirm={vi.fn()} />,
  );
  await userEvent.keyboard('{Escape}');
  expect(onClose).toHaveBeenCalledTimes(1);
  unmount();

  renderWithClient(
    <WarmDaysModal accountId="a1" phone="+79991234567" onClose={onClose} onConfirm={vi.fn()} />,
  );
  await userEvent.click(screen.getByRole('radio', { name: '7 дней' }));
  await userEvent.click(screen.getByText('Отмена'));
  expect(onClose).toHaveBeenCalledTimes(1);
  expect(screen.getByText('Закрыть без сохранения?')).toBeInTheDocument();
});

test('in pool mode it starts every idle account with the picked delay and no spam check', async () => {
  const onConfirm = vi.fn();
  renderWithClient(
    <WarmDaysModal
      poolAccountIds={['a1', 'a2', 'a3']}
      defaultDays={14}
      onClose={vi.fn()}
      onConfirm={onConfirm}
    />,
  );
  expect(screen.getByText('Запуск пула')).toBeInTheDocument();
  expect(
    screen.getByText('3 аккаунта — у каждого свой момент старта в выбранном окне'),
  ).toBeInTheDocument();
  expect(screen.queryByText('Спам-чек')).toBeNull();

  await userEvent.click(screen.getByRole('radio', { name: '24 ч' }));
  await userEvent.click(screen.getByRole('button', { name: 'Запустить пул' }));
  expect(onConfirm).toHaveBeenCalledWith(14, 'normal', 24);
});

test('a picked delay alone also asks before closing', async () => {
  const onClose = vi.fn();
  renderWithClient(
    <WarmDaysModal accountId="a1" phone="+79991234567" onClose={onClose} onConfirm={vi.fn()} />,
  );
  await userEvent.click(screen.getByRole('radio', { name: '6 ч' }));
  await userEvent.click(screen.getByText('Отмена'));
  expect(onClose).not.toHaveBeenCalled();
  expect(screen.getByText('Закрыть без сохранения?')).toBeInTheDocument();
});
