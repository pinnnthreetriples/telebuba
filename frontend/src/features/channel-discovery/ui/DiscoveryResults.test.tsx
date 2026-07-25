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
    render(
      <Harness
        data={board([candidate()], { phase: 'done', last_error: 'telemetr_rate_limited' })}
      />,
    );

    expect(screen.getByText(/telemetr_rate_limited/)).toBeInTheDocument();
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

  it('names each source', () => {
    render(
      <Harness
        data={board([
          candidate({ channel: 'native', source: 'telegram_search' }),
          candidate({ channel: 'similar', source: 'telegram_similar' }),
          candidate({ channel: 'catalogue', source: 'telemetr' }),
        ])}
      />,
    );

    expect(screen.getByText('поиск Telegram')).toBeInTheDocument();
    expect(screen.getByText('похожие')).toBeInTheDocument();
    expect(screen.getByText('Telemetr.io')).toBeInTheDocument();
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
