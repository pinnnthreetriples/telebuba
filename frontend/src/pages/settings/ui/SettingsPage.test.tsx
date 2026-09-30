import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { SettingsPage } from './SettingsPage';

function renderWithClient(ui: ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

const SETTINGS = {
  inter_account_chat: false,
  reactions_enabled: true,
  join_enabled: true,
  enforce_readiness: true,
  has_gemini_key: true,
  gemini_key_hint: 'AIza…x7Qp',
  gemini_model: 'gemini-2.5-flash',
  gemini_max_retries: 2,
  gemini_min_interval_seconds: 1.5,
  updated_at: 'now',
};

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

function routeSettings() {
  vi.mocked(fetch).mockImplementation(() => Promise.resolve(jsonResponse(SETTINGS)));
}

const captchaGroup = () => screen.getByRole('radiogroup', { name: 'LLM для решения капчи' });
const textGroup = () => screen.getByRole('radiogroup', { name: 'LLM для текстов' });

async function warmingPutBody(): Promise<Record<string, unknown>> {
  const calls = vi.mocked(fetch).mock.calls.map(([i]) => i as Request);
  const puts = calls.filter((r) => r.url.endsWith('/warming/settings') && r.method === 'PUT');
  const put = puts[puts.length - 1];
  if (!put) throw new Error('no warming PUT');
  return JSON.parse(await put.clone().text());
}

test('saves only its own fields — never the warming toggles or cached models', async () => {
  routeSettings();
  renderWithClient(<SettingsPage />);
  await screen.findByText('Сохранить');

  await userEvent.click(screen.getByText('Сохранить'));
  await waitFor(async () => {
    const body = await warmingPutBody();
    // The warming board owns these; sending the cached copy back undid its edits.
    for (const foreign of [
      'enforce_readiness',
      'reactions_enabled',
      'join_enabled',
      'inter_account_chat',
      'gemini_model',
      'openai_model',
    ]) {
      expect(body).not.toHaveProperty(foreign);
    }
  });
  expect(await screen.findByText('Сохранено')).toBeInTheDocument();
  const neuroCalls = vi
    .mocked(fetch)
    .mock.calls.filter(([i]) => (i as Request).url.includes('/neurocomment/'));
  expect(neuroCalls).toHaveLength(0);
});

test('the duplicated blocks are gone: warming toggles and both limit cards', async () => {
  routeSettings();
  renderWithClient(<SettingsPage />);
  await screen.findByText('Сохранить');

  for (const gone of [
    'Лимиты прогрева',
    'Лимиты нейрокомментинга',
    'Реакции в прогреве',
    'Вступление в каналы',
    'Чат между аккаунтами',
  ]) {
    expect(screen.queryByText(gone)).not.toBeInTheDocument();
  }
});

test('a DeepSeek key and the DeepSeek captcha provider are saved', async () => {
  routeSettings();
  renderWithClient(<SettingsPage />);
  await screen.findByText('Сохранить');

  // DeepSeek and OpenAI have no stored key in this fixture, in that order.
  const [deepseekField] = screen.getAllByPlaceholderText('Ключ не задан');
  expect(screen.getByText('DeepSeek API key')).toBeInTheDocument();
  await userEvent.type(deepseekField!, 'sk-deepseek-typed');
  await userEvent.click(within(captchaGroup()).getByRole('radio', { name: 'DeepSeek' }));
  await userEvent.click(screen.getByText('Сохранить'));
  await waitFor(async () => {
    const body = await warmingPutBody();
    expect(body.deepseek_api_key).toBe('sk-deepseek-typed');
    expect(body.clear_deepseek_key).toBe(false);
    expect(body.captcha_llm_provider).toBe('deepseek');
  });
});

test('the eye shows the stored key hint, never the key itself', async () => {
  routeSettings();
  renderWithClient(<SettingsPage />);
  await screen.findByText('Сохранить');

  expect(
    screen.getByPlaceholderText('Ключ задан — оставьте пустым, чтобы сохранить'),
  ).toBeInTheDocument();
  const [geminiEye] = screen.getAllByRole('button', { name: 'Показать/скрыть ключ' });
  await userEvent.click(geminiEye!);
  expect(
    screen.getByPlaceholderText('Ключ задан: AIza…x7Qp — оставьте пустым, чтобы сохранить'),
  ).toBeInTheDocument();
});

test('a failed save shows the error state instead of silently doing nothing', async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const url = new URL(request.url);
    if (request.method === 'PUT' && url.pathname === '/api/v1/warming/settings') {
      return Promise.resolve(
        new Response(JSON.stringify({ error: { code: 'internal', message: 'boom' } }), {
          status: 500,
          headers: { 'Content-Type': 'application/json' },
        }),
      );
    }
    return Promise.resolve(jsonResponse(SETTINGS));
  });
  renderWithClient(<SettingsPage />);
  await waitFor(() => {
    expect(screen.getByText('Сохранить')).toBeInTheDocument();
  });

  await userEvent.click(screen.getByText('Сохранить'));
  expect(await screen.findByText('Не удалось сохранить')).toBeInTheDocument();
});

test('the clear-key action sends clear_gemini_key: true', async () => {
  routeSettings();
  renderWithClient(<SettingsPage />);
  await waitFor(() => {
    expect(screen.getByText('Сохранить')).toBeInTheDocument();
  });

  await userEvent.click(screen.getByText('Очистить ключ'));
  // the placeholder reflects the pending clear
  expect(screen.getByPlaceholderText('Ключ будет удалён при сохранении')).toBeInTheDocument();

  await userEvent.click(screen.getByText('Сохранить'));
  await waitFor(async () => {
    expect((await warmingPutBody()).clear_gemini_key).toBe(true);
  });
});

test('retry and pause fields load, show help hints, and are sent in the warming PUT', async () => {
  routeSettings();
  renderWithClient(<SettingsPage />);
  await waitFor(() => {
    expect(screen.getByText('Сохранить')).toBeInTheDocument();
  });

  const retries = screen.getByLabelText('Повторные попытки при ошибке');
  const interval = screen.getByLabelText('Пауза между запросами (сек)');
  // loaded from the settings row
  expect(retries).toHaveValue(2);
  expect(interval).toHaveValue(1.5);
  // each field carries a "?" help hint with a plain-language explanation
  expect(
    screen.getByText(/упереться в лимит запросов в минуту/, { exact: false }),
  ).toBeInTheDocument();

  await userEvent.clear(retries);
  await userEvent.type(retries, '3');
  await userEvent.clear(interval);
  await userEvent.type(interval, '4.5');
  await userEvent.click(screen.getByText('Сохранить'));
  await waitFor(async () => {
    const body = await warmingPutBody();
    expect(body.gemini_max_retries).toBe(3);
    expect(body.gemini_min_interval_seconds).toBe(4.5);
  });
});

test('an out-of-range retry value is clamped before the PUT', async () => {
  routeSettings();
  renderWithClient(<SettingsPage />);
  await waitFor(() => {
    expect(screen.getByText('Сохранить')).toBeInTheDocument();
  });

  const retries = screen.getByLabelText('Повторные попытки при ошибке');
  // Set an over-max value directly (a number input rejects out-of-range typing).
  fireEvent.change(retries, { target: { value: '99' } });
  await userEvent.click(screen.getByText('Сохранить'));
  await waitFor(async () => {
    expect((await warmingPutBody()).gemini_max_retries).toBe(5); // clamped to max
  });
});

test('by default the save sends clear_gemini_key: false (key preserved)', async () => {
  routeSettings();
  renderWithClient(<SettingsPage />);
  await waitFor(() => {
    expect(screen.getByText('Сохранить')).toBeInTheDocument();
  });
  await userEvent.click(screen.getByText('Сохранить'));
  await waitFor(async () => {
    const body = await warmingPutBody();
    expect(body.clear_gemini_key).toBe(false);
    expect(body.gemini_api_key).toBeNull();
  });
});

test('a pasted key is sent trimmed', async () => {
  routeSettings();
  renderWithClient(<SettingsPage />);
  await screen.findByText('Сохранить');

  const [, openaiField] = screen.getAllByPlaceholderText('Ключ не задан');
  await userEvent.type(openaiField!, '  sk-openai-pasted  ');
  await userEvent.click(screen.getByText('Сохранить'));
  await waitFor(async () => {
    expect((await warmingPutBody()).openai_api_key).toBe('sk-openai-pasted');
  });
});

test('the keys come Gemini, DeepSeek, then OpenAI marked as captcha-only', async () => {
  routeSettings();
  renderWithClient(<SettingsPage />);
  await screen.findByText('Сохранить');

  const labels = screen
    .getAllByText(/API key/)
    .map((label) => label.textContent)
    .filter(Boolean);
  expect(labels).toEqual(['Gemini API key', 'DeepSeek API key', 'OpenAI API key (только капча)']);
});

test('the text LLM defaults to DeepSeek and a switch to Gemini is saved', async () => {
  routeSettings();
  renderWithClient(<SettingsPage />);
  await screen.findByText('Сохранить');

  expect(within(textGroup()).getByRole('radio', { name: 'DeepSeek' })).toBeChecked();
  // OpenAI solves captchas only, so it is no text provider.
  expect(within(textGroup()).queryByRole('radio', { name: 'ChatGPT' })).not.toBeInTheDocument();
  await userEvent.click(within(textGroup()).getByRole('radio', { name: 'Gemini' }));
  await userEvent.click(screen.getByText('Сохранить'));
  await waitFor(async () => {
    expect((await warmingPutBody()).text_llm_provider).toBe('gemini');
  });
});

test('a choice without its key warns which model stands in, following the draft', async () => {
  routeSettings();
  renderWithClient(<SettingsPage />);
  await screen.findByText('Сохранить');

  // Only Gemini is keyed: the default DeepSeek hands the texts over.
  const textFallback = 'Ключ DeepSeek не задан — тексты будет писать Gemini.';
  expect(screen.getByText(textFallback)).toBeInTheDocument();
  const [deepseekField] = screen.getAllByPlaceholderText('Ключ не задан');
  await userEvent.type(deepseekField!, 'sk-deepseek-typed');
  expect(screen.queryByText(textFallback)).not.toBeInTheDocument();

  await userEvent.click(within(captchaGroup()).getByRole('radio', { name: 'ChatGPT' }));
  expect(screen.getByText('Ключ OpenAI не задан — капчу будет решать Gemini.')).toBeInTheDocument();

  // A pending clear counts as no key: nothing is left to solve the captcha.
  await userEvent.click(screen.getByText('Очистить ключ'));
  expect(
    screen.getByText('Ключи OpenAI и Gemini не заданы — капча решаться не будет.'),
  ).toBeInTheDocument();
  await userEvent.clear(deepseekField!);
  expect(screen.getByText('Не задан ни один ключ — тексты писать некому.')).toBeInTheDocument();
});
