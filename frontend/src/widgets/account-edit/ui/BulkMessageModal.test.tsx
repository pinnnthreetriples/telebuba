import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { BulkMessageOutcome } from '@/shared/api';

import type { BulkMessageDraft } from './BulkMessageModal';
import { BulkMessageModal } from './BulkMessageModal';

function respond(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function renderModal() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  function Harness() {
    const [jobId, setJobId] = useState<string | null>(null);
    const [visible, setVisible] = useState(true);
    const [draft, setDraft] = useState<BulkMessageDraft | null>(null);
    return visible ? (
      <BulkMessageModal
        jobId={jobId}
        initialDraft={draft}
        onJobStarted={setJobId}
        onNewJob={() => {
          setJobId(null);
        }}
        onDraftSaved={setDraft}
        onClose={() => {
          setVisible(false);
        }}
      />
    ) : (
      <button
        onClick={() => {
          setVisible(true);
        }}
      >
        Reopen
      </button>
    );
  }
  render(
    <QueryClientProvider client={queryClient}>
      <Harness />
    </QueryClientProvider>,
  );
}

function mockApi(
  options: {
    sendError?: string;
    generateError?: string;
    generateDeferred?: Promise<Response>;
    stoppable?: boolean;
    accountsError?: boolean;
    results?: BulkMessageOutcome[];
  } = {},
) {
  let cancelled = false;
  vi.mocked(fetch).mockImplementation(async (input) => {
    const request = input as Request;
    const path = new URL(request.url).pathname;
    if (path === '/api/v1/accounts' && request.method === 'GET') {
      if (options.accountsError) return respond({ detail: 'boom' }, 500);
      return respond({
        items: [
          { account_id: 'a1', status: 'alive', created_at: 'now', updated_at: 'now' },
          { account_id: 'a2', status: 'alive', created_at: 'now', updated_at: 'now' },
        ],
        next_cursor: null,
      });
    }
    if (path === '/api/v1/accounts/bulk-messages' && request.method === 'POST') {
      if (options.sendError) {
        return respond({ error: { code: 'bad_request', message: options.sendError } }, 400);
      }
      return respond({ job_id: 'job-1', status: 'running' });
    }
    if (path === '/api/v1/accounts/bulk-messages/generate' && request.method === 'POST') {
      if (options.generateDeferred) return options.generateDeferred;
      if (options.generateError) {
        return respond({ error: { code: 'bad_request', message: options.generateError } }, 400);
      }
      return respond({ text: 'Generated message', provider: 'deepseek' });
    }
    if (path === '/api/v1/accounts/bulk-messages/job-1/cancel' && request.method === 'POST') {
      cancelled = true;
      return respond({ job_id: 'job-1', status: 'cancelled', total: 4, completed: 0, results: [] });
    }
    if (path === '/api/v1/accounts/bulk-messages/job-1' && request.method === 'GET') {
      return respond({
        job_id: 'job-1',
        status: options.stoppable ? (cancelled ? 'cancelled' : 'running') : 'completed',
        total: 4,
        completed: options.stoppable ? 0 : 4,
        results: options.results ?? [],
      });
    }
    throw new Error(`Unexpected request: ${request.method} ${path}`);
  });
}

async function pickBothAccounts() {
  await userEvent.click(screen.getByRole('button', { name: 'Добавить аккаунты' }));
  const picker = await screen.findByRole('dialog', { name: 'Добавить аккаунты' });
  await waitFor(() => {
    expect(within(picker).getByRole('checkbox', { name: /Выбрать все/ })).toBeEnabled();
  });
  await userEvent.click(within(picker).getByRole('checkbox', { name: /Выбрать все/ }));
  await userEvent.click(within(picker).getByRole('button', { name: 'Добавить (2)' }));
}

test('sends every selected account to every distinct recipient and shows progress', async () => {
  mockApi();
  renderModal();
  const start = screen.getByRole('button', { name: 'Начать отправку' });
  expect(start).toBeDisabled();

  await pickBothAccounts();
  await userEvent.type(screen.getByLabelText('Получатели'), '@first{enter}@second{enter}@first');
  await userEvent.type(screen.getByLabelText('Сообщение'), 'Hello');
  await userEvent.clear(screen.getByLabelText('От, секунд'));
  await userEvent.type(screen.getByLabelText('От, секунд'), '3');
  await userEvent.clear(screen.getByLabelText('До, секунд'));
  await userEvent.type(screen.getByLabelText('До, секунд'), '8');

  expect(screen.getByText('4 отправки')).toBeInTheDocument();
  expect(start).toBeEnabled();
  await userEvent.click(start);

  const request = await waitFor(() => {
    const match = vi.mocked(fetch).mock.calls.find(([input]) => {
      const value = input as Request;
      return (
        value.method === 'POST' && new URL(value.url).pathname === '/api/v1/accounts/bulk-messages'
      );
    });
    expect(match).toBeDefined();
    return match?.[0] as Request;
  });
  expect(await request.clone().json()).toEqual({
    account_ids: ['a1', 'a2'],
    recipients: ['@first', '@second'],
    text: 'Hello',
    min_delay_seconds: 3,
    max_delay_seconds: 8,
  });
  expect(await screen.findByText('4 из 4 отправок')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Готово' }));
  await userEvent.click(screen.getByRole('button', { name: 'Reopen' }));
  expect(screen.getByText('4 из 4 отправок')).toBeInTheDocument();
});

test('shows a specific inline error when recipient aliases duplicate', async () => {
  mockApi({ sendError: 'duplicate recipients' });
  renderModal();
  await pickBothAccounts();
  await userEvent.type(screen.getByLabelText('Получатели'), '@first{enter}t.me/first');
  await userEvent.type(screen.getByLabelText('Сообщение'), 'Hello');
  await userEvent.click(screen.getByRole('button', { name: 'Начать отправку' }));
  expect(
    await screen.findByText(
      'Один получатель указан несколько раз под разными именами или ссылками.',
    ),
  ).toBeInTheDocument();
});

test('shows an inline error when AI generation is unavailable', async () => {
  mockApi({ generateError: 'generator_unavailable' });
  renderModal();
  await userEvent.click(screen.getByRole('button', { name: 'Создать текст с ИИ' }));
  await userEvent.type(screen.getByLabelText('Что написать'), 'Greeting');
  await userEvent.click(screen.getByRole('button', { name: 'Сгенерировать' }));
  expect(
    await screen.findByText('Генерация недоступна: настройте ключ DeepSeek или Gemini.'),
  ).toBeInTheDocument();
});

test('stops a running batch and keeps its completed count', async () => {
  mockApi({ stoppable: true });
  renderModal();
  await pickBothAccounts();
  await userEvent.type(screen.getByLabelText('Получатели'), '@first{enter}@second');
  await userEvent.type(screen.getByLabelText('Сообщение'), 'Hello');
  await userEvent.click(screen.getByRole('button', { name: 'Начать отправку' }));
  await userEvent.click(await screen.findByRole('button', { name: 'Остановить' }));
  await waitFor(() => {
    expect(screen.getByText('Отправка остановлена')).toBeInTheDocument();
  });
  expect(screen.getByText('0 из 4 отправок')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Остановить' })).not.toBeInTheDocument();
});

test('AI generation is optional and inserts editable text', async () => {
  mockApi();
  renderModal();
  await userEvent.click(screen.getByRole('button', { name: 'Создать текст с ИИ' }));
  await userEvent.type(screen.getByLabelText('Что написать'), 'Greeting');
  await userEvent.click(screen.getByRole('button', { name: 'Сгенерировать' }));
  await waitFor(() => {
    expect(screen.getByLabelText('Сообщение')).toHaveValue('Generated message');
  });
  expect(screen.getByText('Создано через DeepSeek. Текст можно изменить.')).toBeInTheDocument();
  await userEvent.type(screen.getByLabelText('Сообщение'), '!');
  expect(screen.getByLabelText('Сообщение')).toHaveValue('Generated message!');
});

test('requires a valid delay range before sending', async () => {
  mockApi();
  renderModal();
  await pickBothAccounts();
  await userEvent.type(screen.getByLabelText('Получатели'), '@first');
  await userEvent.type(screen.getByLabelText('Сообщение'), 'Hello');
  const start = screen.getByRole('button', { name: 'Начать отправку' });
  expect(start).toBeEnabled();
  await userEvent.clear(screen.getByLabelText('От, секунд'));
  await userEvent.type(screen.getByLabelText('От, секунд'), '10');
  expect(start).toBeDisabled();
  await userEvent.clear(screen.getByLabelText('До, секунд'));
  await userEvent.type(screen.getByLabelText('До, секунд'), '12');
  expect(start).toBeEnabled();
});

test('keeps an unsent draft when the dialog is closed and reopened', async () => {
  mockApi();
  renderModal();
  await pickBothAccounts();
  await userEvent.type(screen.getByLabelText('Получатели'), '@first');
  await userEvent.type(screen.getByLabelText('Сообщение'), 'Draft text');
  await userEvent.clear(screen.getByLabelText('От, секунд'));
  await userEvent.type(screen.getByLabelText('От, секунд'), '2');
  await userEvent.click(screen.getByRole('button', { name: 'Отмена' }));
  await userEvent.click(screen.getByRole('button', { name: 'Reopen' }));
  expect(screen.getByText('2/50')).toBeInTheDocument();
  expect(screen.getByLabelText('Получатели')).toHaveValue('@first');
  expect(screen.getByLabelText('Сообщение')).toHaveValue('Draft text');
  expect(screen.getByLabelText('От, секунд')).toHaveValue(2);
});

test('keeps manual edits made while AI generation is pending', async () => {
  let finishGeneration: (response: Response) => void = () => undefined;
  const generateDeferred = new Promise<Response>((resolve) => {
    finishGeneration = resolve;
  });
  mockApi({ generateDeferred });
  renderModal();
  await userEvent.click(screen.getByRole('button', { name: 'Создать текст с ИИ' }));
  await userEvent.type(screen.getByLabelText('Что написать'), 'Greeting');
  await userEvent.click(screen.getByRole('button', { name: 'Сгенерировать' }));
  await userEvent.type(screen.getByLabelText('Сообщение'), 'My own text');
  finishGeneration(respond({ text: 'Generated message', provider: 'deepseek' }));
  await waitFor(() => {
    expect(screen.getByRole('button', { name: 'Сгенерировать' })).toBeEnabled();
  });
  expect(screen.getByLabelText('Сообщение')).toHaveValue('My own text');
});

test('shows a load error and retry in the account picker', async () => {
  mockApi({ accountsError: true });
  renderModal();
  await userEvent.click(screen.getByRole('button', { name: 'Добавить аккаунты' }));
  const picker = await screen.findByRole('dialog', { name: 'Добавить аккаунты' });
  expect(await within(picker).findByRole('alert')).toHaveTextContent(
    'Не удалось загрузить аккаунты',
  );
  expect(within(picker).getByRole('button', { name: 'Повторить' })).toBeInTheDocument();
  expect(within(picker).queryByText('Ничего не найдено')).not.toBeInTheDocument();
});

test('distinguishes failed, skipped, and unconfirmed results with retry waits', async () => {
  mockApi({
    results: [
      { account_id: 'a1', recipient: '@first', status: 'failed', error_code: 'flood_wait' },
      {
        account_id: 'a1',
        recipient: '@second',
        status: 'skipped',
        error_code: 'flood_wait',
        retry_after_seconds: 90,
      },
      {
        account_id: 'a2',
        recipient: '@first',
        status: 'unconfirmed',
        error_code: 'delivery_unconfirmed',
      },
    ],
  });
  renderModal();
  await pickBothAccounts();
  await userEvent.type(screen.getByLabelText('Получатели'), '@first{enter}@second');
  await userEvent.type(screen.getByLabelText('Сообщение'), 'Hello');
  await userEvent.click(screen.getByRole('button', { name: 'Начать отправку' }));
  expect(await screen.findByText('3 результата требуют внимания')).toBeInTheDocument();
  expect(screen.getByText(/Пропущено: Telegram ограничил частоту отправки/)).toBeInTheDocument();
  expect(screen.getByText(/Telegram указал паузу 90 с/)).toBeInTheDocument();
  expect(screen.getByText(/Доставка не подтверждена/)).toBeInTheDocument();
});
