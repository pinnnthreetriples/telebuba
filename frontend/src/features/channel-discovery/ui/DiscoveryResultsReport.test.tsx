import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import '@/shared/i18n';

import type { DiscoveryBoard, DiscoveryCandidate } from '@/shared/api';

import { DiscoveryResults } from './DiscoveryResults';

/** What the results view is allowed to CLAIM about a run.
 *
 * Its own module because `DiscoveryResults.test.tsx` sits on the 700-line test cap.
 * Every case here is a sentence the operator reads as measured: a gate that was
 * cleared, rows a source kept, a total, a subscriber count inside their filter.
 */

function candidate(overrides: Partial<DiscoveryCandidate> = {}): DiscoveryCandidate {
  return {
    channel: 'alpha',
    title: 'Alpha',
    source: 'telegram_search',
    qualification: 'comments_on',
    ...overrides,
  };
}

function board(
  candidates: DiscoveryCandidate[],
  progress: Partial<DiscoveryBoard['progress']> = {},
): DiscoveryBoard {
  return {
    campaign_id: 'c1',
    progress: {
      phase: 'done',
      running: false,
      total: candidates.length,
      qualified: candidates.length,
      comments_on: candidates.length,
      last_error: null,
      ...progress,
    },
    candidates,
  };
}

function Harness({ data }: { data: DiscoveryBoard | undefined }) {
  return (
    <DiscoveryResults
      board={data}
      loading={false}
      errored={false}
      selected={new Set()}
      onToggle={() => undefined}
      onToggleAll={() => undefined}
    />
  );
}

const showAll = () => userEvent.click(screen.getByRole('radio', { name: /Все/ }));
const openDetails = () => userEvent.click(screen.getByRole('button', { name: 'детали' }));

describe('DiscoveryResults reporting', () => {
  it('names a private row for what it is and gives the same reason for both dead rows', async () => {
    // "@id:123" is the backend's PRIVATE_PREFIX ref dressed as a username nobody can open.
    render(
      <Harness
        data={board([
          candidate({ channel: 'id:123', kind: 'channel' }),
          candidate({ channel: 'grp', kind: 'group' }),
          candidate({ channel: 'good' }),
        ])}
      />,
    );
    await showAll();

    expect(screen.getByText('закрытый канал')).toBeInTheDocument();
    expect(screen.queryByText(/@id:123/)).not.toBeInTheDocument();
    const hidden = screen.getByRole('checkbox', { name: 'Выбрать канал закрытый канал' });
    expect(hidden).toBeDisabled();
    expect(hidden).toHaveAttribute('title', 'нельзя добавить в кампанию');
    expect(screen.getByRole('checkbox', { name: 'Выбрать канал grp' })).toHaveAttribute(
      'title',
      'нельзя добавить в кампанию',
    );
    expect(screen.getByRole('checkbox', { name: 'Выбрать канал good' })).not.toHaveAttribute(
      'title',
    );
    // The comments cell states the same reason in words for both dead rows.
    expect(screen.getAllByText('нельзя добавить в кампанию')).toHaveLength(2);
  });

  it('keeps the real badge on a subscription-gated row instead of a reason', async () => {
    render(
      <Harness
        data={board([
          candidate({ channel: 'sub', access: 'subscription', qualification: 'comments_on' }),
        ])}
      />,
    );
    await showAll();

    expect(screen.getByRole('checkbox', { name: 'Выбрать канал sub' })).toBeDisabled();
    expect(screen.getByText('есть')).toBeInTheDocument();
  });

  it('names the membership reason once, in the subtitle, and shows the real badge in the comments cell', async () => {
    render(
      <Harness
        data={board([
          candidate({ channel: 'mine', in_campaign: true }),
          candidate({ channel: 'theirs', taken_by_other_campaign: true }),
        ])}
      />,
    );
    await showAll();

    // The subtitle names the reason — the one place it appears.
    expect(screen.getByText('@mine').parentElement?.textContent).toContain('уже в кампании');
    expect(screen.getByText('@theirs').parentElement?.textContent).toContain('в другой кампании');
    // The comments cell is not asked the same question twice: it shows the row's own
    // qualification badge instead of repeating the membership reason.
    expect(screen.getAllByText('есть')).toHaveLength(2);
    expect(screen.queryByText('уже в кампании')).not.toBeInTheDocument();
    expect(screen.queryByText('в другой кампании')).not.toBeInTheDocument();
  });

  it('leaves a row adoptable regardless of its verdict', () => {
    render(
      <Harness
        data={board([
          candidate({ channel: 'opaque', verdict: null }),
          candidate({ channel: 'gated', verdict: { join_to_send: true, scam: true } }),
        ])}
      />,
    );

    expect(screen.getByRole('checkbox', { name: 'Выбрать канал opaque' })).toBeEnabled();
    expect(screen.getByRole('checkbox', { name: 'Выбрать канал gated' })).toBeEnabled();
  });

  it('spells out each caveat the verdict answered', () => {
    render(
      <Harness
        data={board([
          candidate({
            verdict: {
              group_slowmode_enabled: true,
              join_to_send: true,
              join_request: true,
              scam: true,
              restricted: true,
              can_send_messages: false,
            },
          }),
        ])}
      />,
    );

    expect(screen.getByText(/пауза между комментариями/)).toBeInTheDocument();
    expect(screen.getByText(/нужно вступить в чат/)).toBeInTheDocument();
    expect(screen.getByText(/вступление по заявке/)).toBeInTheDocument();
    expect(screen.getByText(/Telegram пометил как скам/)).toBeInTheDocument();
    expect(screen.getByText(/ограничен Telegram/)).toBeInTheDocument();
    expect(screen.getByText(/писать нельзя/)).toBeInTheDocument();
  });

  it('does not repeat the caveat when both scam and fake are set', () => {
    render(<Harness data={board([candidate({ verdict: { scam: true, fake: true } })])} />);
    expect(screen.getAllByText(/Telegram пометил как скам/)).toHaveLength(1);
  });

  it('stays silent on an unanswered verdict', () => {
    // Every field is tri-state: null means Telegram never answered, never "no" — a
    // caveat either way would be a claim nothing checked.
    render(
      <Harness data={board([candidate({ verdict: { can_send_messages: null, scam: null } })])} />,
    );

    expect(screen.queryByText(/писать нельзя/)).not.toBeInTheDocument();
    expect(screen.queryByText(/скам/)).not.toBeInTheDocument();
  });

  it('reports stale candidates as a caption, not a claim of a fresh find', () => {
    // A flood leaves the previous search's channels on screen.
    render(
      <Harness data={board([candidate({ channel: 'fromlastrun' })], { stale_candidates: true })} />,
    );
    expect(screen.getByText(/Каналов от прошлого поиска: 1/)).toBeInTheDocument();
  });

  it('reports the candidate cap as a ceiling, not a total', () => {
    render(<Harness data={board([candidate()], { capped: true })} />);
    expect(screen.getByText(/это предел за один поиск/)).toBeInTheDocument();
  });

  it('sums the rows the filters cut, and says nothing when they cut none', () => {
    const { unmount } = render(
      <Harness data={board([candidate()], { filtered: { language: 3, access: 2 } })} />,
    );
    expect(screen.getByText('Отфильтровано: 5')).toBeInTheDocument();
    unmount();

    render(<Harness data={board([candidate()], { filtered: {} })} />);
    expect(screen.queryByText(/Отфильтровано/)).not.toBeInTheDocument();
  });

  it('says a row entered the list without a subscriber count', () => {
    render(
      <Harness
        data={board([
          candidate({ channel: 'admitted', subscribers: 300, uncounted: true }),
          candidate({ channel: 'notedmissing', subscribers: 50000 }),
        ])}
      />,
    );

    expect(screen.getAllByText(/принят без счётчика/)).toHaveLength(1);
  });

  it('shows only the deviations from the norm in the subtitle', async () => {
    render(
      <Harness
        data={board([candidate({ kind: 'group', access: 'join_request', language: 'uk' })])}
      />,
    );
    await showAll();

    expect(screen.getByText(/группа/)).toBeInTheDocument();
    expect(screen.getByText(/по заявке/)).toBeInTheDocument();
    expect(screen.getByText(/\buk\b/)).toBeInTheDocument();
  });

  it('marks a subscription-gated row as closed, not by its filter name', async () => {
    render(<Harness data={board([candidate({ access: 'subscription' })])} />);
    await showAll();
    expect(screen.getByText(/закрытый/)).toBeInTheDocument();
  });

  it('never prints the norm values as if they were traits', () => {
    render(<Harness data={board([candidate()])} />);
    expect(screen.queryByText(/^канал$/)).not.toBeInTheDocument();
    expect(screen.queryByText(/открытый/)).not.toBeInTheDocument();
  });

  it('renders a language code it has no translation for as itself', () => {
    render(<Harness data={board([candidate({ language: 'de' })])} />);
    expect(screen.getByText(/\bde\b/)).toBeInTheDocument();
  });

  it('lists adoptable rows first, then by subscribers, within the shown set', async () => {
    render(
      <Harness
        data={board([
          candidate({ channel: 'small', subscribers: 10 }),
          candidate({ channel: 'blockedbig', kind: 'group', subscribers: 1_000_000 }),
          candidate({ channel: 'big', subscribers: 1000 }),
        ])}
      />,
    );
    await showAll();

    const handles = screen.getAllByText(/^@/).map((el) => el.textContent);
    expect(handles).toEqual(['@big', '@small', '@blockedbig']);
  });

  it('shows the sources footer with each source and its kept count', () => {
    render(
      <Harness
        data={board([candidate()], {
          sources: [
            { source: 'telegram_search', state: 'ran', hits: 20, kept: 20 },
            { source: 'telegram_posts', state: 'ran', hits: 28, kept: 28 },
            { source: 'telegram_similar', state: 'ran', hits: 39, kept: 39 },
          ],
        })}
      />,
    );

    expect(screen.getByText(/Источники:/)).toBeInTheDocument();
    expect(screen.getByText(/поиск Telegram 20/)).toBeInTheDocument();
    expect(screen.getByText(/поиск по постам 28/)).toBeInTheDocument();
    expect(screen.getByText(/похожие 39/)).toBeInTheDocument();
  });

  it('opens the source strip from the footer toggle', async () => {
    render(
      <Harness
        data={board([candidate()], {
          sources: [
            { source: 'telegram_search', state: 'ran', hits: 20, kept: 20 },
            {
              source: 'telegram_similar',
              state: 'skipped',
              hits: 0,
              kept: 0,
              reason: 'seed_unusable',
            },
          ],
        })}
      />,
    );

    expect(screen.queryByText(/поиск Telegram: 20 из 20/)).not.toBeInTheDocument();
    await openDetails();
    expect(screen.getByText(/поиск Telegram: 20 из 20/)).toBeInTheDocument();
    expect(screen.getByText(/похожие: не запрашивался/)).toBeInTheDocument();
    expect(screen.getByText(/не похож на корректный хэндл/)).toBeInTheDocument();
  });

  it('names a source whose kept rows were all another source’s too', async () => {
    render(
      <Harness
        data={board([candidate()], {
          sources: [{ source: 'telegram_similar', state: 'ran', hits: 60, kept: 50, exclusive: 0 }],
        })}
      />,
    );
    await openDetails();
    expect(screen.getByText(/только здесь: 0/)).toBeInTheDocument();
  });

  it('does not print an exclusive count the source never measured', async () => {
    const { unmount } = render(
      <Harness
        data={board([candidate()], {
          sources: [{ source: 'telegram_search', state: 'ran', hits: 4, kept: 2 }],
        })}
      />,
    );
    await openDetails();
    expect(screen.getByText(/поиск Telegram: 2 из 4/)).toBeInTheDocument();
    expect(screen.queryByText(/только здесь/)).not.toBeInTheDocument();
    unmount();

    render(
      <Harness
        data={board([candidate()], {
          sources: [{ source: 'telegram_search', state: 'ran', hits: 4, kept: 2, exclusive: 1 }],
        })}
      />,
    );
    await openDetails();
    expect(screen.getByText(/только здесь: 1/)).toBeInTheDocument();
  });

  it('names a source the run stopped early, and why', async () => {
    render(
      <Harness
        data={board([candidate()], {
          sources: [
            {
              source: 'telegram_posts',
              state: 'ran',
              hits: 40,
              kept: 12,
              exclusive: 12,
              reason: 'read_budget',
              truncated: true,
            },
          ],
        })}
      />,
    );
    await openDetails();
    expect(screen.getByText(/поиск по постам: 12 из 40/)).toBeInTheDocument();
    expect(screen.getByText(/\(оборван\)/)).toBeInTheDocument();
    expect(screen.getByText(/кончился лимит чтений/)).toBeInTheDocument();
  });

  it('does not put a truncation notice on a source nobody asked', async () => {
    render(
      <Harness
        data={board([candidate()], {
          sources: [
            {
              source: 'telegram_recommended',
              state: 'skipped',
              reason: 'read_budget',
              truncated: true,
            },
          ],
        })}
      />,
    );
    await openDetails();
    expect(screen.getByText(/не запрашивался — кончился лимит чтений/)).toBeInTheDocument();
    expect(screen.queryByText(/оборван/)).not.toBeInTheDocument();
  });

  it('does not credit a source for rows the run never stored', async () => {
    render(
      <Harness
        data={board([candidate({ channel: 'fromlastrun' })], {
          stale_candidates: true,
          sources: [{ source: 'telegram_search', state: 'ran', hits: 4, kept: 0, exclusive: 0 }],
        })}
      />,
    );
    expect(screen.getByText(/Каналов от прошлого поиска: 1/)).toBeInTheDocument();
    await openDetails();
    expect(screen.getByText(/поиск Telegram: 0 из 4/)).toBeInTheDocument();
  });

  it('keeps the operator’s seed and the sweep’s own recommendations apart', async () => {
    // One shared row would let the wave that ran mask the seed's own refusal.
    render(
      <Harness
        data={board([candidate()], {
          sources: [
            { source: 'telegram_similar', state: 'skipped', reason: 'seed_unusable' },
            { source: 'telegram_recommended', state: 'ran', hits: 9, kept: 9, exclusive: 9 },
          ],
        })}
      />,
    );
    await openDetails();
    expect(screen.getByText(/похожие: не запрашивался/)).toBeInTheDocument();
    expect(screen.getByText(/не похож на корректный хэндл/)).toBeInTheDocument();
    expect(screen.getByText(/похожие на найденные: 9 из 9/)).toBeInTheDocument();
  });

  it('translates the premium-required source reason', () => {
    render(<Harness data={board([candidate()], { last_error: 'premium_required' })} />);
    expect(screen.getByText(/нужен Premium-аккаунт/)).toBeInTheDocument();
  });
});
