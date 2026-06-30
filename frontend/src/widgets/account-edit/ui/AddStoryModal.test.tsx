import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { AddStoryModal } from './AddStoryModal';

function renderWithClient(ui: ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

test('audience, caption and no-forward interact and the modal closes', async () => {
  const onClose = vi.fn();
  renderWithClient(<AddStoryModal accountId="acc-1" onClose={onClose} onPosted={vi.fn()} />);
  expect(screen.getByText('Новая сторис')).toBeInTheDocument();

  await userEvent.click(screen.getByText('Близкие друзья'));
  await userEvent.click(screen.getByText('Публично'));
  await userEvent.click(screen.getByText('Контакты'));

  await userEvent.click(screen.getByText('Запретить пересылку сторис'));

  const caption = screen.getByPlaceholderText('Введите подпись…');
  await userEvent.type(caption, 'привет');
  expect(caption).toHaveValue('привет');

  await userEvent.click(screen.getByLabelText('Закрыть'));
  expect(onClose).toHaveBeenCalled();
});

test('picking media and publishing posts the story', async () => {
  vi.mocked(fetch).mockImplementation(() =>
    Promise.resolve(
      new Response(
        JSON.stringify({ status: 'ok', action_type: 'post_story', account_id: 'acc-1' }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    ),
  );
  const onClose = vi.fn();
  const onPosted = vi.fn();
  renderWithClient(<AddStoryModal accountId="acc-1" onClose={onClose} onPosted={onPosted} />);

  const input = document.body.querySelector('input[type="file"]') as HTMLInputElement;
  fireEvent.change(input, {
    target: { files: [new File(['x'], 's.jpg', { type: 'image/jpeg' })] },
  });
  await userEvent.click(screen.getByText('Опубликовать'));

  await waitFor(() => {
    const posted = vi.mocked(fetch).mock.calls.some(([request]) => {
      const req = request as Request;
      return req.url.endsWith('/accounts/acc-1/story') && req.method === 'POST';
    });
    expect(posted).toBe(true);
  });
  await waitFor(() => {
    expect(onPosted).toHaveBeenCalled();
  });
});
