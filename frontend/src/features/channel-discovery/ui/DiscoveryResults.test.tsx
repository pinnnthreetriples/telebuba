import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';

import '@/shared/i18n';

import type { DiscoveryBoard, DiscoveryCandidate } from '@/shared/api';
import { expectNoAxeViolations } from '@/shared/ui/axe.test-helpers';

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

  it('shows the live progress strip instead of the plain line once the backend reports work', () => {
    render(
      <Harness
        data={board([], {
          phase: 'searching',
          running: true,
          work: {
            stage: 'searching',
            done: 12,
            planned: 34,
            eta_seconds: null,
            started_at: '2026-01-01T00:00:00Z',
            streams: [{ account_id: 'a1', name: 'Alisa', state: 'reading' }],
          },
        })}
        loading
      />,
    );

    expect(screen.getByText('Этап 1 из 2 · Поиск в Telegram')).toBeInTheDocument();
    expect(screen.getByText('12 из 34 запросов')).toBeInTheDocument();
    expect(screen.getByText('Alisa')).toBeInTheDocument();
    expect(screen.queryByText('Ищем каналы…')).not.toBeInTheDocument();
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
    // survives, so blanking the list would drop N rows and every tick on them.
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

  it('defaults to the eligible filter and reveals the rest under "Все"', async () => {
    render(
      <Harness
        data={board([
          candidate({ channel: 'good' }),
          candidate({ channel: 'closed', qualification: 'comments_off' }),
        ])}
      />,
    );

    expect(screen.getByText('@good')).toBeInTheDocument();
    expect(screen.queryByText('@closed')).not.toBeInTheDocument();
    expect(screen.getByRole('radio', { name: 'Подходящие · 1' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: 'Все · 2' })).toBeInTheDocument();

    await userEvent.click(screen.getByRole('radio', { name: 'Все · 2' }));

    expect(screen.getByText('@good')).toBeInTheDocument();
    expect(screen.getByText('@closed')).toBeInTheDocument();
  });

  it('renders the badge states distinctly', async () => {
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
    // 'off' is not adoptable, so it is hidden under the default filter.
    await userEvent.click(screen.getByRole('radio', { name: /Все/ }));

    expect(screen.getByText('есть')).toBeInTheDocument();
    expect(screen.getByText('нет')).toBeInTheDocument();
    expect(screen.getByText('проверяем…')).toBeInTheDocument();
  });

  it('labels an unanswerable probe as not checked', () => {
    render(<Harness data={board([candidate({ qualification: 'unknown' })])} />);
    expect(screen.getByText('не проверено')).toBeInTheDocument();
  });

  it('stops pulsing an unprobed row once the run is over', () => {
    // Polling has stopped, so this badge would pulse forever.
    render(<Harness data={board([candidate({ qualification: 'pending' })], { phase: 'done' })} />);

    const badge = screen.getByText('не проверено');
    expect(badge.className).not.toContain('tb-pulse');
  });

  it('settles a stopped run whatever phase it claims', () => {
    // A backend restart forgets the in-memory phase but still serves the stored rows:
    // 'idle' with running:false is polling-stopped, so nothing may pulse.
    render(
      <Harness
        data={board([candidate({ qualification: 'pending' })], { phase: 'idle', running: false })}
      />,
    );

    const badge = screen.getByText('не проверено');
    expect(badge.className).not.toContain('tb-pulse');
  });

  it('disables the checkbox of an ineligible row', async () => {
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

    // Switch to "Все" so the ineligible rows are on screen too.
    await userEvent.click(screen.getByRole('radio', { name: /Все/ }));

    expect(screen.getByRole('checkbox', { name: 'Выбрать канал good' })).toBeEnabled();
    expect(screen.getByRole('checkbox', { name: 'Выбрать канал closed' })).toBeDisabled();
    expect(screen.getByRole('checkbox', { name: 'Выбрать канал mine' })).toBeDisabled();
    expect(screen.getByRole('checkbox', { name: 'Выбрать канал theirs' })).toBeDisabled();
  });

  it('de-emphasises ineligible rows', async () => {
    render(
      <Harness
        data={board([
          candidate({ channel: 'good' }),
          candidate({ channel: 'closed', qualification: 'comments_off' }),
        ])}
      />,
    );
    await userEvent.click(screen.getByRole('radio', { name: /Все/ }));

    const closedRow = screen.getByText('@closed').closest('.border-t');
    const goodRow = screen.getByText('@good').closest('.border-t');
    expect(closedRow?.className).toContain('text-content-subtle');
    expect(goodRow?.className).not.toContain('text-content-subtle');
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
    await userEvent.click(screen.getByRole('radio', { name: /Все/ }));

    await userEvent.click(screen.getByRole('checkbox', { name: 'Выбрать все подходящие' }));

    expect(screen.getByRole('checkbox', { name: 'Выбрать канал good' })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: 'Выбрать канал alsogood' })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: 'Выбрать канал closed' })).not.toBeChecked();
  });

  it('select-all acts on the eligible rows whichever filter is showing', async () => {
    // The eligible set never changes size across the two views — "Все" only adds
    // ineligible rows on top — so select-all's target is the same either way.
    render(
      <Harness
        data={board([
          candidate({ channel: 'good' }),
          candidate({ channel: 'closed', qualification: 'comments_off' }),
        ])}
      />,
    );

    await userEvent.click(screen.getByRole('checkbox', { name: 'Выбрать все подходящие' }));
    expect(screen.getByRole('checkbox', { name: 'Выбрать канал good' })).toBeChecked();

    await userEvent.click(screen.getByRole('radio', { name: /Все/ }));
    expect(screen.getByRole('checkbox', { name: 'Выбрать канал good' })).toBeChecked();
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

  // Below 1024 the list stacks instead of showing the column header row — and
  // select-all lives in that row, so it moves into a label above the rows there.
  it('keeps select-all reachable on a narrow viewport', async () => {
    (
      window as unknown as { happyDOM: { setViewport: (v: { width: number }) => void } }
    ).happyDOM.setViewport({ width: 375 });
    try {
      render(<Harness data={board([candidate({ channel: 'good' })])} />);
      expect(screen.queryByText('Канал')).toBeNull();

      const all = () => screen.getByRole('checkbox', { name: 'Выбрать все подходящие' });
      await userEvent.click(all());
      expect(screen.getByRole('checkbox', { name: 'Выбрать канал good' })).toBeChecked();
    } finally {
      (
        window as unknown as { happyDOM: { setViewport: (v: { width: number }) => void } }
      ).happyDOM.setViewport({ width: 1024 });
    }
  });

  it('stacks a row into two lines on a narrow viewport and keeps a long caveat from forcing it wide', () => {
    (
      window as unknown as { happyDOM: { setViewport: (v: { width: number }) => void } }
    ).happyDOM.setViewport({ width: 375 });
    try {
      render(
        <Harness
          data={board([
            candidate({
              channel: 'gated',
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

      // The wide layout's single row (checkbox + title + subscribers + comments) is
      // gone — subscribers and comments moved to a second line under the title.
      const firstLine = screen.getByText('@gated').closest('.gap-md');
      expect(firstLine?.textContent).not.toContain('пауза между комментариями');

      // min-w-0 on the wrapper is what lets that second line's long, joined caveat
      // text wrap instead of refusing to shrink and pushing the row past the
      // viewport — a flex item's default min-width is its content's own width.
      const caveatWrap = screen.getByText(/пауза между комментариями/).closest('.flex-1');
      expect(caveatWrap?.className).toContain('min-w-0');
    } finally {
      (
        window as unknown as { happyDOM: { setViewport: (v: { width: number }) => void } }
      ).happyDOM.setViewport({ width: 1024 });
    }
  });

  it('measures its own box even when the first frame was the searching state', () => {
    // The container query is read once per ref, on its first commit — and the first
    // commit of a real run is «Ищем каналы…». A box that only existed once rows arrived
    // was never measured, and a ~960px viewport got two select-alls.
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

      expect(screen.getByText('Канал')).toBeInTheDocument();
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

  it('shows the progress strip above the list during qualifying, in place of the small counter', () => {
    render(
      <Harness
        data={board([candidate()], {
          phase: 'qualifying',
          running: true,
          total: 8,
          qualified: 3,
          work: {
            stage: 'qualifying',
            done: 3,
            planned: 8,
            eta_seconds: null,
            started_at: '2026-01-01T00:00:00Z',
            streams: [{ account_id: 'a1', name: 'Alisa', state: 'reading' }],
          },
        })}
      />,
    );

    expect(screen.getByText('Этап 2 из 2 · Проверка комментариев')).toBeInTheDocument();
    expect(screen.getByText('3 из 8 каналов')).toBeInTheDocument();
    // The strip's own header carries the count now — the old isolated counter would
    // otherwise repeat it side by side.
    expect(screen.queryByText('Проверяем комментарии: 3/8')).not.toBeInTheDocument();
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

  it('shows a problem line with a reason and a details toggle', async () => {
    render(<Harness data={board([candidate()], { phase: 'done', last_error: 'seed_unusable' })} />);

    expect(screen.getByRole('status')).toHaveTextContent(/не похож на корректный хэндл/);
    // The details are collapsed until asked for.
    expect(screen.queryByText('проверяется')).not.toBeInTheDocument();

    // "подробнее" expands the source report, not a second copy of the summary line
    // already shown above it.
    await userEvent.click(screen.getByRole('button', { name: 'подробнее' }));
    expect(screen.getAllByText(/не похож на корректный хэндл/)).toHaveLength(1);
  });

  it('shows a generic problem line when a source failed without an abort reason', () => {
    render(
      <Harness
        data={board([candidate()], {
          sources: [{ source: 'telegram_search', state: 'failed' }],
        })}
      />,
    );

    expect(screen.getByText('Часть источников не ответила')).toBeInTheDocument();
  });

  it('shows no problem line when nothing failed', () => {
    render(<Harness data={board([candidate()])} />);
    expect(screen.queryByRole('button', { name: 'подробнее' })).not.toBeInTheDocument();
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

  it('has no axe violations', async () => {
    const { container } = render(
      <Harness
        data={board([
          candidate({ channel: 'good' }),
          candidate({ channel: 'closed', qualification: 'comments_off' }),
        ])}
      />,
    );
    await expectNoAxeViolations(container);
    await userEvent.click(screen.getByRole('radio', { name: /Все/ }));
    await expectNoAxeViolations(container);
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

    await userEvent.click(screen.getByRole('radio', { name: /Все/ }));
    await userEvent.click(screen.getByRole('checkbox', { name: 'Выбрать канал closed' }));

    expect(onToggle).not.toHaveBeenCalled();
  });
});
