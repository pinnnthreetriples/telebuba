import { useTranslation } from 'react-i18next';

import type { WarmingAccountState, WarmingTodayActions } from '@/shared/api';
import { Icon, type IconName } from '@/shared/ui';

import { TickDial } from './TickDial';

// The card's headline, laid out like a food tracker's day: what today was made of —
// one column of dots per kind of action, filled from the bottom like a glass — and,
// beside it, the stint's worked days on the tick dial. The breakdown is the daily
// totals the engine settles (`today`); the headline count is `daily_settled`, so a
// running visit's reservation is never shown as done.
const COLS = 3;
const ROWS = 6;
const CELLS = COLS * ROWS;
const DOT_STAGGER_MS = 14;

type Kind = keyof WarmingTodayActions;

const KINDS: readonly { kind: Kind; icon: IconName; on: string; off: string; ink: string }[] = [
  {
    kind: 'reads',
    icon: 'eye',
    on: 'bg-action-primary',
    off: 'bg-info-hairline',
    ink: 'text-action-primary',
  },
  {
    kind: 'reactions',
    icon: 'sparkles',
    on: 'bg-warning-press',
    off: 'bg-warning-tint',
    ink: 'text-warning-deep',
  },
  {
    kind: 'joins',
    icon: 'user-plus',
    on: 'bg-success',
    off: 'bg-success-tint',
    ink: 'text-success-deep',
  },
  { kind: 'dms', icon: 'send', on: 'bg-danger', off: 'bg-danger-tint', ink: 'text-danger' },
];

// Cells in reading order (top row first) that are lit when `count` fill from the
// bottom-left, row by row, the way liquid fills a glass.
function lit(count: number): boolean[] {
  const filled = Math.min(CELLS, Math.max(0, count));
  return Array.from({ length: CELLS }, (_, index) => {
    const rowFromBottom = ROWS - 1 - Math.floor(index / COLS);
    return rowFromBottom * COLS + (index % COLS) < filled;
  });
}

function Column({
  count,
  icon,
  on,
  off,
  ink,
  label,
  offset,
}: {
  count: number;
  icon: IconName;
  on: string;
  off: string;
  ink: string;
  label: string;
  offset: number;
}) {
  return (
    <div className="flex flex-col items-center gap-2" title={`${label}: ${String(count)}`}>
      <div className="grid grid-cols-3 gap-1" aria-hidden>
        {lit(count).map((cell, index) => (
          <span
            key={index}
            className={`size-dot rounded-full ${cell ? `tb-dot ${on}` : off}`}
            style={
              cell
                ? { animationDelay: `${String((offset + index) * DOT_STAGGER_MS)}ms` }
                : undefined
            }
          />
        ))}
      </div>
      <span className={`flex items-center gap-1 type-small-medium tabular-nums ${ink}`}>
        <Icon name={icon} size={12} className="shrink-0" />
        {count}
        <span className="sr-only">{label}</span>
      </span>
    </div>
  );
}

export function TodayBlock({
  account,
  days,
  target,
  idle,
}: {
  account: WarmingAccountState;
  days: number;
  target: number;
  idle: number;
}) {
  const { t } = useTranslation();
  const cap = account.daily_cap ?? 0;
  const done = account.daily_settled ?? 0;
  const booked = account.state === 'active' ? Math.max(0, (account.daily_actions ?? 0) - done) : 0;
  const today = account.today ?? {};
  const share = target > 0 ? Math.min(1, days / target) : 0;
  return (
    <div className="rounded-md bg-canvas p-4">
      <div className="flex items-baseline justify-between gap-2">
        <span className="type-h3 text-content-primary">{t('warming.card.todayTitle')}</span>
        <span className="type-small text-content-muted">
          <span className="type-small-medium tabular-nums text-content-primary">{done}</span>
          {cap > 0 ? ` / ${String(cap)}` : ''} {t('warming.card.todayUnit', { count: cap || done })}
        </span>
      </div>
      <div className="mt-3 flex flex-wrap items-center justify-around gap-x-3 gap-y-4">
        <div className="shrink-0">
          <div className="flex gap-2">
            {KINDS.map((item, column) => (
              <Column
                key={item.kind}
                count={today[item.kind] ?? 0}
                icon={item.icon}
                on={item.on}
                off={item.off}
                ink={item.ink}
                label={t(`warming.card.kind.${item.kind}`)}
                offset={column * CELLS}
              />
            ))}
          </div>
        </div>
        <div className="flex shrink-0 flex-col items-center">
          <TickDial
            size={96}
            share={share}
            tone={share >= 1 ? 'text-success' : 'text-action-primary'}
            label={t('warming.card.dayProgress', { days, target, count: target })}
          >
            <span className="type-h1 tabular-nums text-content-primary">{days}</span>
            <span className="type-small text-content-muted">
              {t('warming.card.daysOf', { target, count: target })}
            </span>
          </TickDial>
          <span className="type-small-medium text-content-secondary">
            {t('warming.inProgress.days')}
          </span>
          {idle > 0 ? (
            <span className="type-small text-content-subtle">
              {t('warming.card.idleShort', { count: idle })}
            </span>
          ) : null}
        </div>
      </div>
      {booked > 0 ? (
        <div className="mt-3 type-small text-content-muted">
          {t('warming.card.todayBooked', { count: booked })}
        </div>
      ) : null}
    </div>
  );
}
