import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import '@/shared/i18n';

import type { DiscoveryStream, DiscoveryWork } from '@/shared/api';
import { expectNoAxeViolations } from '@/shared/ui/axe.test-helpers';

import { SearchProgress } from './SearchProgress';

function stream(overrides: Partial<DiscoveryStream> = {}): DiscoveryStream {
  return {
    account_id: 'a1',
    name: 'Alisa',
    state: 'reading',
    ...overrides,
  };
}

function work(overrides: Partial<DiscoveryWork> = {}): DiscoveryWork {
  return {
    stage: 'searching',
    done: 12,
    planned: 34,
    eta_seconds: null,
    started_at: '2026-01-01T00:00:00Z',
    streams: [stream()],
    ...overrides,
  };
}

describe('SearchProgress', () => {
  it('shows the stage-1 header and its reads count', () => {
    render(<SearchProgress work={work()} phase="searching" />);
    expect(screen.getByText('Этап 1 из 2 · Поиск в Telegram')).toBeInTheDocument();
    expect(screen.getByText('12 из 34 запросов')).toBeInTheDocument();
  });

  it('shows the stage-2 header and its channels count', () => {
    render(<SearchProgress work={work({ stage: 'qualifying' })} phase="qualifying" />);
    expect(screen.getByText('Этап 2 из 2 · Проверка комментариев')).toBeInTheDocument();
    expect(screen.getByText('12 из 34 каналов')).toBeInTheDocument();
  });

  it('omits the ETA when eta_seconds is null', () => {
    render(<SearchProgress work={work({ eta_seconds: null })} phase="searching" />);
    expect(screen.getByText('12 из 34 запросов')).toBeInTheDocument();
    expect(screen.queryByText(/≈/)).not.toBeInTheDocument();
  });

  it('formats a sub-90s ETA rounded to 5 seconds', () => {
    render(<SearchProgress work={work({ eta_seconds: 40 })} phase="searching" />);
    expect(screen.getByText('12 из 34 запросов · ≈ 40 с')).toBeInTheDocument();
  });

  it('formats a 90s-or-over ETA rounded to whole minutes', () => {
    render(<SearchProgress work={work({ eta_seconds: 130 })} phase="searching" />);
    expect(screen.getByText('12 из 34 запросов · ≈ 2 мин')).toBeInTheDocument();
  });

  it('puts role=status/aria-live on the header line', () => {
    render(<SearchProgress work={work()} phase="searching" />);
    const status = screen.getByRole('status');
    expect(status).toHaveAttribute('aria-live', 'polite');
  });

  it('renders a determinate bar with the done/planned aria values', () => {
    render(<SearchProgress work={work({ done: 12, planned: 34 })} phase="searching" />);
    const bar = screen.getByRole('progressbar');
    expect(bar).toHaveAttribute('aria-valuemin', '0');
    expect(bar).toHaveAttribute('aria-valuemax', '34');
    expect(bar).toHaveAttribute('aria-valuenow', '12');
  });

  it('renders an indeterminate bar with no aria-valuenow when nothing is planned yet', () => {
    render(<SearchProgress work={work({ done: 0, planned: 0 })} phase="searching" />);
    const bar = screen.getByRole('progressbar');
    expect(bar).not.toHaveAttribute('aria-valuenow');
    expect(bar.className).toContain('tb-pulse');
  });

  it('renders one chip per stream, with the state as its dot class and title', () => {
    render(
      <SearchProgress
        work={work({
          streams: [
            stream({ account_id: 'a1', name: 'Alisa', state: 'reading' }),
            stream({ account_id: 'a2', name: 'Katya', state: 'done' }),
          ],
        })}
        phase="searching"
      />,
    );
    expect(screen.getByText('Alisa')).toBeInTheDocument();
    expect(screen.getByText('Katya')).toBeInTheDocument();
    const alisaChip = screen.getByTitle('читает');
    expect(alisaChip.querySelector('span')?.className).toContain('bg-action-primary');
    const katyaChip = screen.getByTitle('готово');
    expect(katyaChip.querySelector('span')?.className).toContain('bg-success');
  });

  it('appends the error to the chip title when the stream carries one', () => {
    render(
      <SearchProgress
        work={work({ streams: [stream({ state: 'flooded', error: 'FloodWait(120s)' })] })}
        phase="searching"
      />,
    );
    expect(screen.getByTitle('упёрся в лимит Telegram · FloodWait(120s)')).toBeInTheDocument();
  });

  it('shows a Premium badge only for a premium stream', () => {
    render(
      <SearchProgress
        work={work({
          streams: [
            stream({ account_id: 'a1', name: 'Alisa', premium: true }),
            stream({ account_id: 'a2', name: 'Katya', premium: false }),
          ],
        })}
        phase="searching"
      />,
    );
    expect(screen.getAllByText('Premium')).toHaveLength(1);
  });

  it('shows no problems line while every stream is healthy', () => {
    render(
      <SearchProgress
        work={work({
          streams: [
            stream({ account_id: 'a1', state: 'reading' }),
            stream({ account_id: 'a2', state: 'done' }),
          ],
        })}
        phase="searching"
      />,
    );
    expect(screen.queryByText(/Выбыли/)).not.toBeInTheDocument();
    expect(screen.queryByText('Все аккаунты выбыли')).not.toBeInTheDocument();
  });

  it('names the dropped streams and their state when some but not all are out', () => {
    render(
      <SearchProgress
        work={work({
          streams: [
            stream({ account_id: 'a1', name: 'Alisa', state: 'flooded' }),
            stream({ account_id: 'a2', name: 'Katya', state: 'offline' }),
            stream({ account_id: 'a3', name: 'Vera', state: 'reading' }),
          ],
        })}
        phase="searching"
      />,
    );
    expect(
      screen.getByText(
        'Выбыли: Alisa (упёрся в лимит Telegram), Katya (нет связи) — поиск продолжается на остальных',
      ),
    ).toBeInTheDocument();
  });

  it('says every account dropped when none is left', () => {
    render(
      <SearchProgress
        work={work({
          streams: [
            stream({ account_id: 'a1', name: 'Alisa', state: 'dead' }),
            stream({ account_id: 'a2', name: 'Katya', state: 'cooling' }),
          ],
        })}
        phase="searching"
      />,
    );
    expect(screen.getByText('Все аккаунты выбыли')).toBeInTheDocument();
    expect(screen.queryByText(/^Выбыли:/)).not.toBeInTheDocument();
  });

  it('has no axe violations', async () => {
    const { container } = render(
      <SearchProgress
        work={work({
          streams: [
            stream({ account_id: 'a1', name: 'Alisa', state: 'flooded', premium: true }),
            stream({ account_id: 'a2', name: 'Katya', state: 'reading' }),
          ],
        })}
        phase="qualifying"
      />,
    );
    await expectNoAxeViolations(container);
  });
});
