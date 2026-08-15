import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';

import '@/shared/i18n';

import { EMPTY_FORM, type DiscoveryFormState } from '../model/discovery';
import { DiscoveryForm } from './DiscoveryForm';

function Harness({
  onSubmit = vi.fn(),
  initial = EMPTY_FORM,
}: {
  onSubmit?: () => void;
  initial?: DiscoveryFormState;
}) {
  const [form, setForm] = useState(initial);
  return <DiscoveryForm form={form} submitting={false} onChange={setForm} onSubmit={onSubmit} />;
}

// The keyword suggester is a TanStack mutation, so even the specs that never touch it
// need a client. No retries: a spec about a failed request should not wait for three.
function renderForm(props: Parameters<typeof Harness>[0] = {}) {
  const queryClient = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <Harness {...props} />
    </QueryClientProvider>,
  );
}

const submitButton = () => screen.getByRole('button', { name: 'Найти' });
const suggestButton = () => screen.getByRole('button', { name: 'Подобрать слова' });

type Suggestion = { keywords?: string[]; error?: string | null };

/** Answer only the expand endpoint, recording every request the form made. */
function routeSuggest(reply: Suggestion | 'hang' | 'fail') {
  const calls: { path: string; body: unknown }[] = [];
  vi.mocked(fetch).mockImplementation(async (input) => {
    const request = input as Request;
    const url = new URL(request.url);
    calls.push({
      path: url.pathname,
      body: request.method === 'POST' ? JSON.parse(await request.clone().text()) : null,
    });
    // Never settles: the only way to observe the in-flight state.
    if (reply === 'hang') return new Promise<Response>(() => undefined);
    if (reply === 'fail') {
      return new Response(JSON.stringify({ error: { code: 'internal', message: 'boom' } }), {
        status: 500,
        headers: { 'Content-Type': 'application/json' },
      });
    }
    return new Response(JSON.stringify({ keywords: [], error: null, ...reply }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  });
  return calls;
}

const keywordsField = () => screen.getByPlaceholderText('крипта, трейдинг, новости');

describe('DiscoveryForm', () => {
  it('disables submit until a long-enough keyword is typed', async () => {
    renderForm();
    expect(submitButton()).toBeDisabled();

    await userEvent.type(screen.getByRole('textbox', { name: /Ключевые слова|крипта/i }), 'abc');
    expect(submitButton()).toBeDisabled();

    await userEvent.type(screen.getByRole('textbox', { name: /Ключевые слова|крипта/i }), 'd');
    expect(submitButton()).toBeEnabled();
  });

  it('reports how many keywords were parsed', async () => {
    renderForm();
    const input = screen.getByPlaceholderText('крипта, трейдинг, новости');

    await userEvent.type(input, 'crypto, trading, ab');

    // 'ab' is below the minimum, so only two are counted.
    expect(screen.getByText(/Распознано: 2/)).toBeInTheDocument();
  });

  it('submits on the button and on Enter', async () => {
    const onSubmit = vi.fn();
    renderForm({ onSubmit, initial: { ...EMPTY_FORM, keywords: 'crypto' } });

    await userEvent.click(submitButton());
    expect(onSubmit).toHaveBeenCalledTimes(1);

    await userEvent.type(screen.getByPlaceholderText('крипта, трейдинг, новости'), '{Enter}');
    expect(onSubmit).toHaveBeenCalledTimes(2);
  });

  it('does not submit an empty form on Enter', async () => {
    const onSubmit = vi.fn();
    renderForm({ onSubmit });

    await userEvent.type(screen.getByPlaceholderText('крипта, трейдинг, новости'), '{Enter}');

    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('keeps the hint prose out of the seed field name', () => {
    renderForm();
    // Nested in the label, the tooltip text joined the input's accessible name.
    expect(screen.getByRole('textbox', { name: 'Похожие на канал' })).toBeInTheDocument();
  });

  it('names the tokens it dropped', async () => {
    renderForm();

    await userEvent.type(screen.getByPlaceholderText('крипта, трейдинг, новости'), 'crypto ab');

    expect(screen.getByText(/Пропущено: ab/)).toBeInTheDocument();
  });

  it('explains subscriber bounds the wrong way round instead of going dead', () => {
    renderForm({
      initial: { ...EMPTY_FORM, keywords: 'crypto', minSubscribers: '900', maxSubscribers: '100' },
    });

    expect(screen.getByText(/«Подписчиков от» больше/)).toBeInTheDocument();
    expect(submitButton()).toBeDisabled();
  });

  it('says what the subscriber bounds actually do', () => {
    // They only reach hits Telegram returned a count for. Unsaid, a row that plainly
    // breaks the filter reads as a broken filter.
    renderForm();

    expect(screen.getByText(/Границы применяются только к находкам/)).toBeInTheDocument();
  });

  it('resets every field', async () => {
    renderForm({ initial: { ...EMPTY_FORM, keywords: 'crypto', minSubscribers: '500' } });

    await userEvent.click(screen.getByRole('button', { name: 'Сбросить' }));

    expect(screen.getByPlaceholderText('крипта, трейдинг, новости')).toHaveValue('');
    expect(submitButton()).toBeDisabled();
  });
});

describe('DiscoveryForm keyword suggester', () => {
  it('sends the typed field as the topic and appends what came back', async () => {
    const calls = routeSuggest({ keywords: ['драки', 'единоборства'] });
    renderForm({ initial: { ...EMPTY_FORM, keywords: 'ММА' } });

    await userEvent.click(suggestButton());

    await waitFor(() => {
      expect(keywordsField()).toHaveValue('ММА, драки, единоборства');
    });
    expect(calls).toEqual([
      { path: '/api/v1/neurocomment/discovery/keywords', body: { topic: 'ММА' } },
    ]);
  });

  it('drops a multi-word suggestion whole, never as a fragment', async () => {
    // This field is separator-delimited and the search posts exactly `parseKeywords`
    // of it, so a phrase let through would enter as fragments.
    routeSuggest({ keywords: ['драки', 'бои без правил', 'единоборства'] });
    renderForm({ initial: { ...EMPTY_FORM, keywords: 'ММА' } });

    await userEvent.click(suggestButton());

    await waitFor(() => {
      expect(keywordsField()).toHaveValue('ММА, драки, единоборства');
    });
    // Not even its longest token: 'правил' would read like a keyword the operator has
    // no reason to doubt, and then spend a Telegram read out of the run's budget.
    expect((keywordsField() as HTMLInputElement).value).not.toContain('правил');
  });

  it('never starts a search', async () => {
    // Every keyword costs a real Telegram read out of the run's budget, so pressing
    // this button must not commit the operator to a search they have not reviewed.
    const onSubmit = vi.fn();
    const calls = routeSuggest({ keywords: ['трейдинг'] });
    renderForm({ onSubmit, initial: { ...EMPTY_FORM, keywords: 'crypto' } });

    await userEvent.click(suggestButton());

    await waitFor(() => {
      expect(keywordsField()).toHaveValue('crypto, трейдинг');
    });
    expect(onSubmit).not.toHaveBeenCalled();
    expect(calls.filter((call) => call.path.includes('/discovery/search'))).toEqual([]);
  });

  it('keeps the operator words when the cap bites, and does not duplicate them', async () => {
    // Ten typed words already fill MAX_KEYWORDS, so nothing suggested can displace
    // them — and 'crypto' comes back from the model as well.
    const typed = 'crypto alpha bravo charlie delta echo foxtrot golf hotel india';
    routeSuggest({ keywords: ['crypto', 'juliett'] });
    renderForm({ initial: { ...EMPTY_FORM, keywords: typed } });

    await userEvent.click(suggestButton());

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Подобрать слова' })).toBeEnabled();
    });
    expect(keywordsField()).toHaveValue(typed);
  });

  it('adds only the suggestions that survive the merge', async () => {
    routeSuggest({ keywords: ['crypto', 'ab', 'trading'] });
    renderForm({ initial: { ...EMPTY_FORM, keywords: 'crypto, ' } });

    await userEvent.click(suggestButton());

    // 'crypto' is already there and 'ab' is below the minimum: only 'trading' lands,
    // and the operator's trailing separator does not become a doubled one.
    await waitFor(() => {
      expect(keywordsField()).toHaveValue('crypto, trading');
    });
  });

  it.each([
    ['llm_unavailable', /ключ DeepSeek не задан/],
    ['llm_failed', /Модель не ответила/],
    ['llm_empty', /не нашлось подходящих слов/],
  ])('explains the %s answer', async (code, message) => {
    routeSuggest({ keywords: [], error: code });
    renderForm({ initial: { ...EMPTY_FORM, keywords: 'crypto' } });

    await userEvent.click(suggestButton());

    expect(await screen.findByText(message)).toBeInTheDocument();
    expect(keywordsField()).toHaveValue('crypto');
  });

  it('falls back to the raw code for an error it has no copy for', async () => {
    routeSuggest({ keywords: [], error: 'llm_moon_phase' });
    renderForm({ initial: { ...EMPTY_FORM, keywords: 'crypto' } });

    await userEvent.click(suggestButton());

    expect(await screen.findByText('llm_moon_phase')).toBeInTheDocument();
  });

  it('says so when the request itself never landed', async () => {
    routeSuggest('fail');
    renderForm({ initial: { ...EMPTY_FORM, keywords: 'crypto' } });

    await userEvent.click(suggestButton());

    expect(await screen.findByText(/Не удалось запросить подбор/)).toBeInTheDocument();
  });

  it('is disabled while empty and while the request is in flight', async () => {
    routeSuggest('hang');
    renderForm();

    expect(suggestButton()).toBeDisabled();

    await userEvent.type(keywordsField(), 'crypto');
    expect(suggestButton()).toBeEnabled();

    await userEvent.click(suggestButton());

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Подбираем…' })).toBeDisabled();
    });
  });

  it('refuses a topic past the endpoint length limit instead of truncating it', async () => {
    const calls = routeSuggest({ keywords: ['trading'] });
    renderForm({ initial: { ...EMPTY_FORM, keywords: 'ю'.repeat(65) } });

    expect(suggestButton()).toBeDisabled();
    // Silently cutting the topic would ask the model about something else, and letting
    // it through would 422.
    expect(screen.getByText(/Тема длиннее 64 символов/)).toBeInTheDocument();
    expect(calls).toEqual([]);
  });

  it('merges into what the operator typed while the answer was on the way', async () => {
    let settle: (value: Response) => void = () => undefined;
    vi.mocked(fetch).mockImplementation(
      async () =>
        new Promise<Response>((resolve) => {
          settle = resolve;
        }),
    );
    renderForm({ initial: { ...EMPTY_FORM, keywords: 'crypto' } });

    await userEvent.click(suggestButton());
    await userEvent.type(keywordsField(), ', stocks');
    settle(
      new Response(JSON.stringify({ keywords: ['trading'], error: null }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );

    // The keystrokes typed during the flight survive: the merge reads the latest form,
    // not the one captured when the button was pressed.
    await waitFor(() => {
      expect(keywordsField()).toHaveValue('crypto, stocks, trading');
    });
  });
});
