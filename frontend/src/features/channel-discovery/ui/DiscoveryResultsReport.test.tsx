import { render, screen } from '@testing-library/react';
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
      comments_on: candidates.filter((item) => item.qualification === 'comments_on').length,
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

describe('DiscoveryResults reporting', () => {
  it('says so when the reply carried a group but not its write rights', () => {
    // ChannelForbidden/ChatEmpty answer none of the group gates, so the row rendered as
    // a channel measured and cleared on all of them.
    render(
      <Harness
        data={board([
          candidate({
            qualification: 'comments_on',
            verdict: { can_send_messages: null, scam: false, fake: false, restricted: false },
          }),
        ])}
      />,
    );

    expect(screen.getByText('пригодность не проверена')).toBeInTheDocument();
  });

  it('leaves a fully answered row without an unknown notice', () => {
    render(
      <Harness data={board([candidate({ verdict: { can_send_messages: true, scam: false } })])} />,
    );

    expect(screen.queryByText('пригодность не проверена')).not.toBeInTheDocument();
  });

  it('does not credit a source for rows the run never stored', () => {
    // A flood leaves the previous search's channels on screen; counting them as this
    // run's find, with a source credited for keeping them, describes rows nobody has.
    render(
      <Harness
        data={board([candidate({ channel: 'fromlastrun' })], {
          stale_candidates: true,
          sources: [{ source: 'telegram_search', state: 'ran', hits: 4, kept: 0, exclusive: 0 }],
        })}
      />,
    );

    expect(screen.getByText(/Каналов от прошлого поиска: 1/)).toBeInTheDocument();
    expect(screen.queryByText(/Найдено каналов/)).not.toBeInTheDocument();
    expect(screen.getByText(/поиск Telegram: 0 из 4/)).toBeInTheDocument();
  });

  it('reports the candidate cap as a ceiling, not a total', () => {
    render(<Harness data={board([candidate()], { capped: true })} />);

    expect(screen.getByText(/это предел за один поиск/)).toBeInTheDocument();
  });

  it('says a row entered the list without a subscriber count', () => {
    // The bounds could not be applied to it, and the number beside it came from the
    // comment check afterwards — so it may plainly break the operator's own filter.
    render(
      <Harness
        data={board([
          candidate({ channel: 'admitted', subscribers: 300, uncounted: true }),
          candidate({ channel: 'filtered', subscribers: 50000 }),
        ])}
      />,
    );

    expect(screen.getAllByText('принят без счётчика')).toHaveLength(1);
  });

  it('does not put a truncation notice on a source nobody asked', () => {
    // "not queried (stopped early) — the read budget ran out": a source that was never
    // asked did not stop early, and two of the three clauses said the same thing.
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

    expect(screen.getByText(/не запрашивался — кончился лимит чтений/)).toBeInTheDocument();
    expect(screen.queryByText(/оборван/)).not.toBeInTheDocument();
  });

  it('badges what kind of place a row is, how it is entered and its language', () => {
    render(
      <Harness
        data={board([candidate({ kind: 'group', access: 'join_request', language: 'uk' })])}
      />,
    );

    expect(screen.getByText('группа')).toBeInTheDocument();
    expect(screen.getByText('по заявке')).toBeInTheDocument();
    expect(screen.getByText('uk')).toBeInTheDocument();
  });

  it('shows no trait badge on a row stored before the fields existed', () => {
    // A guessed "channel" would be a claim; an empty cell is not.
    render(<Harness data={board([candidate()])} />);

    expect(screen.queryByText('канал')).not.toBeInTheDocument();
    expect(screen.queryByText('открытый')).not.toBeInTheDocument();
  });

  it('renders a trait code it has no translation for as itself', () => {
    render(<Harness data={board([candidate({ kind: 'forum', access: 'invite_only' })])} />);

    expect(screen.getByText('forum')).toBeInTheDocument();
    expect(screen.getByText('invite_only')).toBeInTheDocument();
  });

  it('sums the rows the filters cut, and says nothing when they cut none', () => {
    // A narrow filter and an empty Telegram must not both read as "found 1".
    const { unmount } = render(
      <Harness data={board([candidate()], { filtered: { language: 3, access: 2 } })} />,
    );
    expect(screen.getByText('Отфильтровано: 5')).toBeInTheDocument();
    unmount();

    render(<Harness data={board([candidate()], { filtered: {} })} />);
    expect(screen.queryByText(/Отфильтровано/)).not.toBeInTheDocument();
  });

  it('renders a source label it has no translation for', () => {
    // The candidate table outlives the build that wrote it, so a migrated row names a
    // source this one does not have — as a label, never as a raw i18n key.
    render(<Harness data={board([candidate({ source: 'telemetr', sources: ['telemetr'] })])} />);

    expect(screen.getByText('telemetr')).toBeInTheDocument();
  });
});
