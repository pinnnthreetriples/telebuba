import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test } from 'vitest';

import '@/shared/i18n';

import type { AccountRead } from '@/shared/api';

import {
  ago,
  ahead,
  CLEAN,
  json,
  LIMITED,
  renderWithClient,
  routeApi,
} from './SpamCard.test-helpers';
import { SignalsSection } from './SignalsSection';

function render(account: AccountRead) {
  routeApi({});
  renderWithClient(<SignalsSection account={account} />);
}

// The row whose label is `label`, so an assertion reads its own value and nothing beside it.
function row(label: string): HTMLElement {
  const el = screen.getByText(label).closest<HTMLElement>('div.flex');
  if (!el) throw new Error(`no row for "${label}"`);
  return el;
}

test('the chip, its confidence and the source line say what decided and when', () => {
  render({ ...LIMITED, spam_observed_at: ago(72) });
  expect(within(row('Текущий статус')).getByText('Ограничен')).toBeInTheDocument();
  expect(within(row('Текущий статус')).getByText('уверенность: высокая')).toBeInTheDocument();
  expect(within(row('Источник')).getByText('@SpamBot, 3 дня назад')).toBeInTheDocument();
});

test('a passive source is named in the same line', () => {
  render({
    ...LIMITED,
    spam_confidence: 'medium',
    spam_source: 'peer_flood',
    spam_observed_at: ago(2),
  });
  expect(within(row('Источник')).getByText('PeerFlood, 2 часа назад')).toBeInTheDocument();
  expect(screen.getByText('уверенность: средняя')).toBeInTheDocument();
});

test('an account nobody has looked at says there is no data, not a source', () => {
  render({ ...CLEAN, spam_verdict: 'unknown', spam_status: 'unknown', spam_source: 'none' });
  expect(within(row('Источник')).getByText('нет данных')).toBeInTheDocument();
  expect(within(row('Текущий статус')).getByText('Неизвестно')).toBeInTheDocument();
});

test('a dated limit says until when; an undated one says indefinitely', () => {
  const until = ahead(24 * 5);
  const { unmount } = (() => {
    routeApi({});
    return renderWithClient(<SignalsSection account={{ ...LIMITED, spam_valid_until: until }} />);
  })();
  expect(within(row('Последний spam-block')).getByText(/^ограничен до /)).toBeInTheDocument();
  unmount();

  render({ ...LIMITED, spam_valid_until: null });
  expect(within(row('Последний spam-block')).getByText('бессрочно')).toBeInTheDocument();
});

test('a frozen account is its own verdict, never "limited until"', () => {
  render({ ...LIMITED, spam_verdict: 'frozen', spam_source: 'freeze', spam_observed_at: ago(1) });
  expect(within(row('Текущий статус')).getByText('Заморожен')).toBeInTheDocument();
  expect(within(row('Последний spam-block')).getByText('аккаунт заморожен')).toBeInTheDocument();
  expect(within(row('Источник')).getByText('Заморозка, 1 час назад')).toBeInTheDocument();
});

test('the next check and the seven-day count show when the backend sent them', () => {
  render({ ...CLEAN, spam_next_probe_at: ahead(5), spam_probes_7d: 4 });
  expect(within(row('Следующая проверка')).getByText(/^возможна /)).toBeInTheDocument();
  expect(within(row('Проверок за 7 дней')).getByText('4')).toBeInTheDocument();
});

test('a next check already in the past is not announced', () => {
  render({ ...CLEAN, spam_next_probe_at: ago(1), spam_probes_7d: 0 });
  expect(screen.queryByText('Следующая проверка')).toBeNull();
  expect(within(row('Проверок за 7 дней')).getByText('0')).toBeInTheDocument();
});

test('the platform restriction is a flag with its reason beside the verdict, not a spam limit', () => {
  render({ ...CLEAN, spam_tos_restricted: true, spam_tos_reason: 'terms violation' });
  // The chip stays clean: a restriction never moves the spam verdict.
  expect(within(row('Текущий статус')).getByText('Без ограничений')).toBeInTheDocument();
  expect(within(row('Последний spam-block')).getByText('не зафиксирован')).toBeInTheDocument();
  expect(
    within(row('Ограничение Telegram')).getByText('не спам-блок · terms violation'),
  ).toBeInTheDocument();
});

test('no flag, no restriction row', () => {
  render({ ...CLEAN, spam_tos_restricted: false });
  expect(screen.queryByText('Ограничение Telegram')).toBeNull();
});

test('a payload that predates the verdict fields still renders as it did', () => {
  render({
    ...CLEAN,
    spam_verdict: undefined,
    spam_source: undefined,
    spam_status: 'limited',
    spam_detail: 'до 2026-07-01',
  });
  expect(within(row('Текущий статус')).getByText('Ограничен')).toBeInTheDocument();
  expect(screen.getByText('до 2026-07-01')).toBeInTheDocument();
  expect(screen.queryByText('Источник')).toBeNull();
});

test('a held check shows its reason and when it can be asked again, never an empty answer', async () => {
  routeApi({
    'POST /api/v1/accounts/acc-1/spam-check': () =>
      json({
        account_id: 'acc-1',
        status: 'clean',
        checked_at: 'now',
        served_from: 'deferred',
        reason: 'proxy_spacing',
        retry_at: ahead(2),
        probed: false,
      }),
  });
  renderWithClient(<SignalsSection account={CLEAN} />);

  await userEvent.click(screen.getByRole('button', { name: 'Проверить' }));
  // The card is a collapsible: the answer is read with the section open, as the operator would.
  await userEvent.click(screen.getByText('Спам/бан-сигналы'));

  const notice = await screen.findByRole('status');
  expect(within(notice).getByText('Проверка не отправлена')).toBeInTheDocument();
  expect(within(notice).getByText(/за тем же прокси/)).toBeInTheDocument();
  expect(within(notice).getByText(/Спросить снова можно /)).toBeInTheDocument();
});

test('an answer from the window shows that the last reading was handed back', async () => {
  routeApi({
    'POST /api/v1/accounts/acc-1/spam-check': () =>
      json({
        account_id: 'acc-1',
        status: 'clean',
        checked_at: 'now',
        served_from: 'coalesced',
        next_allowed_at: ahead(1),
        probed: false,
      }),
  });
  renderWithClient(<SignalsSection account={CLEAN} />);

  await userEvent.click(screen.getByRole('button', { name: 'Проверить' }));
  // The card is a collapsible: the answer is read with the section open, as the operator would.
  await userEvent.click(screen.getByText('Спам/бан-сигналы'));

  const notice = await screen.findByRole('status');
  expect(within(notice).getByText(/только что проверяли/)).toBeInTheDocument();
  expect(within(notice).getByText(/Спросить снова можно /)).toBeInTheDocument();
});

test('a refusal the card does not know still says something', async () => {
  routeApi({
    'POST /api/v1/accounts/acc-1/spam-check': () =>
      json({
        account_id: 'acc-1',
        status: 'unknown',
        checked_at: 'now',
        served_from: 'refused',
        reason: 'brand_new_reason',
        probed: false,
      }),
  });
  renderWithClient(<SignalsSection account={CLEAN} />);

  await userEvent.click(screen.getByRole('button', { name: 'Проверить' }));

  await userEvent.click(screen.getByText('Спам/бан-сигналы'));
  expect(await screen.findByText('Проверка сейчас не отправляется.')).toBeVisible();
});

test('a limited account carries the appeal block under the signals', async () => {
  routeApi({});
  renderWithClient(<SignalsSection account={LIMITED} />);
  expect(await screen.findByTestId('spam-appeal')).toBeInTheDocument();
});
