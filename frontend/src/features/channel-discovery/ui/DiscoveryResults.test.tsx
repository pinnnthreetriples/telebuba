import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';

import '@/shared/i18n';

import type { DiscoveryBoard, DiscoveryCandidate } from '@/shared/api';

import { DiscoveryResults } from './DiscoveryResults';

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

function Harness({
  data,
  loading = false,
  errored = false,
}: {
  data: DiscoveryBoard | undefined;
  loading?: boolean;
  errored?: boolean;
}) {
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  return (
    <DiscoveryResults
      board={data}
      loading={loading}
      errored={errored}
      selected={selected}
      onToggle={(channel) => {
        setSelected((current) => {
          const next = new Set(current);
          if (next.has(channel)) next.delete(channel);
          else next.add(channel);
          return next;
        });
      }}
      onToggleAll={(channels, next) => {
        setSelected(next ? new Set(channels) : new Set());
      }}
    />
  );
}

describe('DiscoveryResults', () => {
  it('shows a searching state before any candidate arrives', () => {
    render(<Harness data={undefined} loading />);
    expect(screen.getByText('Ищем каналы…')).toBeInTheDocument();
  });

  it('hides the previous run rows while the next search runs', () => {
    render(
      <Harness
        data={board([candidate({ channel: 'stale' })], { phase: 'searching', running: true })}
        loading
      />,
    );

    expect(screen.getByText('Ищем каналы…')).toBeInTheDocument();
    expect(screen.queryByText('@stale')).not.toBeInTheDocument();
  });

  it('says the request failed instead of claiming nothing was found', () => {
    render(<Harness data={undefined} errored />);
    expect(screen.getByText(/Не удалось получить результаты/)).toBeInTheDocument();
    expect(screen.queryByText(/Ничего не нашлось/)).not.toBeInTheDocument();
  });

  it('keeps the rows a failed refetch left in the cache', () => {
    // TanStack v5 sets status 'error' on a failed refetch while the cached frame
    // survives, so blanking the table would drop N rows and every tick on them.
    render(<Harness data={board([candidate({ channel: 'good' })])} errored />);

    expect(screen.getByText('@good')).toBeInTheDocument();
    expect(screen.queryByText(/Не удалось получить результаты/)).not.toBeInTheDocument();
  });

  it('shows an empty state when the search found nothing', () => {
    render(<Harness data={board([])} />);
    expect(screen.getByText(/Ничего не нашлось/)).toBeInTheDocument();
  });

  it('shows the abort reason when the run failed with no results', () => {
    render(<Harness data={board([], { phase: 'failed', last_error: 'FloodWait(300s)' })} />);
    expect(screen.getByText(/FloodWait\(300s\)/)).toBeInTheDocument();
  });

  it('shows the abort reason even when the run kept partial results', () => {
    render(
      <Harness data={board([candidate()], { phase: 'failed', last_error: 'FloodWait(300s)' })} />,
    );

    expect(screen.getByText(/Прервано: FloodWait\(300s\)/)).toBeInTheDocument();
    // the partial results stay adoptable
    expect(screen.getByText('@alpha')).toBeInTheDocument();
  });

  it('renders the three comment states distinctly', () => {
    render(
      <Harness
        data={board(
          [
            candidate({ channel: 'on', qualification: 'comments_on' }),
            candidate({ channel: 'off', qualification: 'comments_off' }),
            candidate({ channel: 'waiting', qualification: 'pending' }),
          ],
          { phase: 'qualifying', running: true },
        )}
      />,
    );

    // role=img so the icon-only verdicts expose their label to a screen reader
    expect(screen.getByRole('img', { name: 'Комментарии включены' })).toBeInTheDocument();
    expect(screen.getByRole('img', { name: 'Комментарии выключены' })).toBeInTheDocument();
    expect(screen.getByText('проверяется')).toBeInTheDocument();
  });

  it('labels a failed probe as unknown, not pending', () => {
    render(<Harness data={board([candidate({ qualification: 'unknown' })])} />);
    expect(screen.getByText('не удалось проверить')).toBeInTheDocument();
  });

  it('stops animating an unprobed row once the run is over', () => {
    // Polling has stopped, so this row would pulse forever.
    render(<Harness data={board([candidate({ qualification: 'pending' })], { phase: 'done' })} />);

    const cell = screen.getByText('не проверено');
    expect(cell.querySelector('.animate-pulse')).toBeNull();
  });

  it('tells a never-probed row apart from an unanswerable one', () => {
    // Opposite next steps: "не проверено" means re-run and it resolves, "не удалось
    // проверить" means the probe already gave up on it.
    render(
      <Harness
        data={board(
          [
            candidate({ channel: 'unprobed', qualification: 'pending' }),
            candidate({ channel: 'opaque', qualification: 'unknown' }),
          ],
          { phase: 'failed', last_error: 'FloodWait(300s)' },
        )}
      />,
    );

    expect(screen.getByText('не проверено')).toBeInTheDocument();
    expect(screen.getByText('не удалось проверить')).toBeInTheDocument();
  });

  it('settles a stopped run whatever phase it claims', () => {
    // A backend restart forgets the in-memory phase but still serves the stored rows:
    // 'idle' with running:false is polling-stopped, so nothing may animate.
    render(
      <Harness
        data={board([candidate({ qualification: 'pending' })], { phase: 'idle', running: false })}
      />,
    );

    const cell = screen.getByText('не проверено');
    expect(cell.querySelector('.animate-pulse')).toBeNull();
  });

  it('disables the checkbox of an ineligible row', () => {
    render(
      <Harness
        data={board([
          candidate({ channel: 'good' }),
          candidate({ channel: 'closed', qualification: 'comments_off' }),
          candidate({ channel: 'mine', in_campaign: true }),
          candidate({ channel: 'theirs', taken_by_other_campaign: true }),
        ])}
      />,
    );

    expect(screen.getByRole('checkbox', { name: 'Выбрать канал good' })).toBeEnabled();
    expect(screen.getByRole('checkbox', { name: 'Выбрать канал closed' })).toBeDisabled();
    expect(screen.getByRole('checkbox', { name: 'Выбрать канал mine' })).toBeDisabled();
    expect(screen.getByRole('checkbox', { name: 'Выбрать канал theirs' })).toBeDisabled();
  });

  it('marks membership with a pill', () => {
    render(
      <Harness
        data={board([
          candidate({ channel: 'mine', in_campaign: true }),
          candidate({ channel: 'theirs', taken_by_other_campaign: true }),
        ])}
      />,
    );

    expect(screen.getByText('уже в кампании')).toBeInTheDocument();
    expect(screen.getByText('занят другой кампанией')).toBeInTheDocument();
  });

  it('de-emphasises ineligible rows', () => {
    render(
      <Harness
        data={board([
          candidate({ channel: 'good' }),
          candidate({ channel: 'closed', qualification: 'comments_off' }),
        ])}
      />,
    );

    const closedRow = screen.getByText('@closed').closest('tr');
    const goodRow = screen.getByText('@good').closest('tr');
    expect(closedRow?.className).toContain('opacity-60');
    expect(goodRow?.className).not.toContain('opacity-60');
  });

  it('select-all ticks only the eligible rows', async () => {
    render(
      <Harness
        data={board([
          candidate({ channel: 'good' }),
          candidate({ channel: 'alsogood' }),
          candidate({ channel: 'closed', qualification: 'comments_off' }),
        ])}
      />,
    );

    await userEvent.click(screen.getByRole('checkbox', { name: 'Выбрать все подходящие' }));

    expect(screen.getByRole('checkbox', { name: 'Выбрать канал good' })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: 'Выбрать канал alsogood' })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: 'Выбрать канал closed' })).not.toBeChecked();
  });

  it('reports a partial selection as indeterminate', async () => {
    render(
      <Harness
        data={board([candidate({ channel: 'good' }), candidate({ channel: 'alsogood' })])}
      />,
    );

    await userEvent.click(screen.getByRole('checkbox', { name: 'Выбрать канал good' }));

    const all = screen.getByRole<HTMLInputElement>('checkbox', {
      name: 'Выбрать все подходящие',
    });
    expect(all.indeterminate).toBe(true);
    expect(all.checked).toBe(false);
  });

  it('select-all clears the selection on a second click', async () => {
    render(<Harness data={board([candidate({ channel: 'good' })])} />);
    // Re-query after each click: the header cell re-renders, so a captured node
    // can go stale.
    const all = () => screen.getByRole('checkbox', { name: 'Выбрать все подходящие' });

    await userEvent.click(all());
    expect(screen.getByRole('checkbox', { name: 'Выбрать канал good' })).toBeChecked();

    await userEvent.click(all());
    expect(screen.getByRole('checkbox', { name: 'Выбрать канал good' })).not.toBeChecked();
  });

  // Below 1024 DataTable renders cards, which have no column headers — and the
  // select-all lives in one, so it moves into the toolbar there.
  // This covers the narrow side only. What catches a *duplicate* select-all is the
  // wide-viewport tests above (and in ChannelDiscoveryModal.test.tsx), which query it
  // by accessible name at the default 1024px and throw on two matches.
  it('keeps select-all reachable on a narrow viewport', async () => {
    (
      window as unknown as { happyDOM: { setViewport: (v: { width: number }) => void } }
    ).happyDOM.setViewport({ width: 375 });
    try {
      render(<Harness data={board([candidate({ channel: 'good' })])} />);
      expect(screen.queryByRole('table')).toBeNull();

      const all = () => screen.getByRole('checkbox', { name: 'Выбрать все подходящие' });
      await userEvent.click(all());
      expect(screen.getByRole('checkbox', { name: 'Выбрать канал good' })).toBeChecked();
    } finally {
      (
        window as unknown as { happyDOM: { setViewport: (v: { width: number }) => void } }
      ).happyDOM.setViewport({ width: 1024 });
    }
  });

  it('measures its own box even when the first frame was the searching state', () => {
    // The container query is read once per ref, on its first commit — and the first
    // commit of a real run is «Ищем каналы…». A box that only existed once rows arrived
    // was never measured: the toolbar fell back to the viewport (narrow) while DataTable
    // measured the wide box, and a ~960px viewport got two select-alls.
    const viewport = (width: number) => {
      (
        window as unknown as { happyDOM: { setViewport: (v: { width: number }) => void } }
      ).happyDOM.setViewport({ width });
    };
    viewport(375);
    vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(900);
    try {
      const { rerender } = render(<Harness data={undefined} loading />);
      rerender(<Harness data={board([candidate({ channel: 'good' })])} />);

      expect(screen.getByRole('table')).toBeInTheDocument();
      expect(screen.getAllByRole('checkbox', { name: 'Выбрать все подходящие' })).toHaveLength(1);
    } finally {
      vi.restoreAllMocks();
      viewport(1024);
    }
  });

  it('shows qualification progress while the pass runs', () => {
    render(
      <Harness
        data={board([candidate()], {
          phase: 'qualifying',
          running: true,
          total: 8,
          qualified: 3,
        })}
      />,
    );

    expect(screen.getByText('Проверяем комментарии: 3/8')).toBeInTheDocument();
  });

  it('keeps the qualification count after the run aborts', () => {
    render(
      <Harness
        data={board([candidate()], {
          phase: 'failed',
          running: false,
          total: 300,
          qualified: 40,
          last_error: 'FloodWait(300s)',
        })}
      />,
    );

    // How far the aborted run got is the number the operator acts on.
    expect(screen.getByText('Проверяем комментарии: 40/300')).toBeInTheDocument();
  });

  it('announces the transient states in a live region', () => {
    const { unmount } = render(<Harness data={undefined} loading />);
    expect(screen.getByRole('status')).toHaveTextContent('Ищем каналы…');
    unmount();

    render(<Harness data={undefined} errored />);
    expect(screen.getByRole('status')).toHaveTextContent(/Не удалось получить результаты/);
  });

  it('surfaces a degraded source once the run settles', () => {
    render(<Harness data={board([candidate()], { phase: 'done', last_error: 'seed_unusable' })} />);

    // The code is translated, not printed: the operator used to read the literal string
    // "seed_unusable" off the board.
    expect(screen.getByText(/не похож на корректный хэндл/)).toBeInTheDocument();
    expect(screen.queryByText(/seed_unusable/)).not.toBeInTheDocument();
  });

  it('keeps a degraded source visible through the qualifying phase', () => {
    // Qualifying is the longest phase of a run: suppressing the banner there made a
    // known source failure vanish for tens of seconds and then come back.
    render(
      <Harness
        data={board([candidate()], {
          phase: 'qualifying',
          running: true,
          last_error: 'seed_unusable',
        })}
      />,
    );

    expect(screen.getByText(/не похож на корректный хэндл/)).toBeInTheDocument();
  });

  it('credits every source, so one that reached nothing is visible', () => {
    // The reported bug: a run reached "done" and nothing said that one of its sources
    // had contributed zero rows.
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

    expect(screen.getByText(/поиск Telegram: 20 из 20/)).toBeInTheDocument();
    expect(screen.getByText(/похожие: не запрашивался/)).toBeInTheDocument();
    expect(screen.getByText(/не похож на корректный хэндл/)).toBeInTheDocument();
  });

  it('names a source whose kept rows were all another source’s too', () => {
    // "50 of 60" hid the variant: every row this source found ALONE was cut by the cap,
    // so it looks like a major contributor while contributing nothing unique.
    render(
      <Harness
        data={board([candidate()], {
          sources: [{ source: 'telegram_similar', state: 'ran', hits: 60, kept: 50, exclusive: 0 }],
        })}
      />,
    );

    expect(screen.getByText(/только здесь: 0/)).toBeInTheDocument();
  });

  it('renders subscribers compactly and an em dash when unknown', () => {
    render(
      <Harness
        data={board([
          candidate({ channel: 'big', subscribers: 12345 }),
          candidate({ channel: 'unknowncount', subscribers: null }),
        ])}
      />,
    );

    expect(screen.getByText('12,3 тыс.')).toBeInTheDocument();
    expect(screen.getByText('—')).toBeInTheDocument();
  });

  it('names a source the run stopped early, and why', () => {
    // "12 of 40" alone reads as a source read to the end; the budget cut it, so the
    // counts are a floor and the operator can win more rows by narrowing the run.
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

    expect(screen.getByText(/поиск по постам: 12 из 40/)).toBeInTheDocument();
    expect(screen.getByText(/\(оборван\)/)).toBeInTheDocument();
    expect(screen.getByText(/кончился лимит чтений/)).toBeInTheDocument();
  });

  it('keeps the operator’s seed and the sweep’s own recommendations apart', () => {
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

    expect(screen.getByText(/похожие: не запрашивался/)).toBeInTheDocument();
    expect(screen.getByText(/не похож на корректный хэндл/)).toBeInTheDocument();
    expect(screen.getByText(/похожие на найденные: 9 из 9/)).toBeInTheDocument();
  });

  it('shows the whole path that found a channel, not just one label', () => {
    // Two independent waves reaching the same channel is the stronger signal, and
    // `source` names only the winner of the dedup.
    render(
      <Harness
        data={board([
          candidate({
            channel: 'both',
            source: 'telegram_search',
            sources: ['telegram_search', 'telegram_recommended'],
          }),
        ])}
      />,
    );

    expect(screen.getByText('поиск Telegram + похожие на найденные')).toBeInTheDocument();
  });

  it('falls back to the single source when no provenance came through', () => {
    render(<Harness data={board([candidate({ channel: 'lone', source: 'telegram_posts' })])} />);
    expect(screen.getByText('поиск по постам')).toBeInTheDocument();
  });

  it('reads a missing verdict as unknown, not as a cleared channel', () => {
    // The verdict is not persisted, so every row served after a restart carries none.
    render(<Harness data={board([candidate({ qualification: 'comments_on' })])} />);

    expect(screen.getByText('пригодность не проверена')).toBeInTheDocument();
  });

  it('marks a channel whose discussion group bans writing', () => {
    render(<Harness data={board([candidate({ verdict: { can_send_messages: false } })])} />);

    expect(screen.getByText(/запрещено писать/)).toBeInTheDocument();
  });

  it('never turns an unanswered field into a cleared gate', () => {
    // Every field is tri-state: null means Telegram did not answer, never "no". A mark
    // either way would be a claim nothing checked — and NO mark is what a channel
    // cleared on every gate looks like, so the absence has to be said out loud. Asserting
    // only the missing marks passed against exactly the row this is meant to catch: a
    // green comments tick with no mark and no notice.
    render(
      <Harness
        data={board([
          candidate({
            verdict: {
              can_send_messages: null,
              join_to_send: null,
              join_request: null,
              group_slowmode_enabled: null,
              scam: null,
              fake: null,
              restricted: null,
            },
          }),
        ])}
      />,
    );

    expect(screen.queryByText(/запрещено писать/)).not.toBeInTheDocument();
    expect(screen.queryByText(/нужно вступить/)).not.toBeInTheDocument();
    expect(screen.queryByText(/медленный режим/)).not.toBeInTheDocument();
    expect(screen.queryByText(/скам/)).not.toBeInTheDocument();
    expect(screen.getByText('пригодность не проверена')).toBeInTheDocument();
  });

  it('spells out the gates the backend did answer', () => {
    render(
      <Harness
        data={board([
          candidate({
            verdict: {
              join_to_send: true,
              join_request: true,
              group_slowmode_enabled: true,
              scam: true,
              fake: true,
              restricted: true,
            },
          }),
        ])}
      />,
    );

    expect(screen.getByText(/нужно вступить в чат обсуждения/)).toBeInTheDocument();
    expect(screen.getByText(/по заявке — её одобряет админ/)).toBeInTheDocument();
    expect(screen.getByText('в чате обсуждения включён медленный режим')).toBeInTheDocument();
    expect(screen.getByText(/как скам/)).toBeInTheDocument();
    expect(screen.getByText(/как фейк/)).toBeInTheDocument();
    expect(screen.getByText(/ограничил канал/)).toBeInTheDocument();
  });

  it('states a group slow mode without inventing an interval for it', () => {
    // The interval would cost a second getFullChannel, so the mark states the fact and
    // prints no number rather than standing "—" in for one.
    render(<Harness data={board([candidate({ verdict: { group_slowmode_enabled: true } })])} />);

    expect(screen.getByText('в чате обсуждения включён медленный режим')).toBeInTheDocument();
    expect(screen.queryAllByText(/медленный режим/)).toHaveLength(1);
  });

  it('leaves an unknown verdict adoptable', () => {
    // Only comments_off disables a row; a verdict nobody could read is not a refusal.
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

  it('does not repeat "not checked" as an unknown verdict', () => {
    render(<Harness data={board([candidate({ qualification: 'pending' })], { phase: 'done' })} />);

    expect(screen.getByText('не проверено')).toBeInTheDocument();
    expect(screen.queryByText('пригодность не проверена')).not.toBeInTheDocument();
  });

  it('does not call back when a disabled checkbox is clicked', async () => {
    const onToggle = vi.fn();
    render(
      <DiscoveryResults
        board={board([candidate({ channel: 'closed', qualification: 'comments_off' })])}
        loading={false}
        errored={false}
        selected={new Set()}
        onToggle={onToggle}
        onToggleAll={vi.fn()}
      />,
    );

    await userEvent.click(screen.getByRole('checkbox', { name: 'Выбрать канал closed' }));

    expect(onToggle).not.toHaveBeenCalled();
  });
});
