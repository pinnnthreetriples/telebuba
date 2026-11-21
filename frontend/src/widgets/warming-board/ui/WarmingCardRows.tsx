import type { TFunction } from 'i18next';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { ReactNode } from 'react';

import type { WarmingAccountState } from '@/shared/api';
import { cn, formatLocalTime } from '@/shared/lib';
import { Icon, type IconName, ProgressBar } from '@/shared/ui';

import { countdown, MINUTE_MS } from '../lib/countdown';
import { readinessReasonKey, trustTone } from '../lib/trust';

// The operator-console rows of a warming card (Прогрев 2.0, item 2.3). The backend
// sends codes — phase, DM status, the one attention item — and these rows only word
// them; none of them re-derives a decision the engine already made.

// Re-renders once a minute, enough for an "h:mm" countdown.
function useMinuteClock(): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => {
      setNow(Date.now());
    }, MINUTE_MS);
    return () => {
      clearInterval(id);
    };
  }, []);
  return now;
}

// Чип карточки: тонкая пилюля с рамкой, текст строки — её подпись.
const CHIP = 'inline-flex items-center gap-1 rounded-full border border-line px-2';

// Row 2: phase with its bar to the next one, persona, trust as a colour, spam status.
export function PhaseRow({ account }: { account: WarmingAccountState }) {
  const { t } = useTranslation();
  const phase = account.phase ?? 'intro';
  const trust = account.trust_score;
  const spam =
    account.spam_status === 'clean' || account.spam_status === 'limited'
      ? account.spam_status
      : 'unknown';
  const next = account.days_to_next_phase;
  const phaseLabel = t('warming.console.phaseLabel', {
    phase: t(`warming.console.phase.${phase}`),
  });
  return (
    <div className="mb-3 flex flex-wrap items-center gap-1 type-small text-content-muted">
      {/* Фаза — главный чип: своя мини-полоса до следующей фазы внутри него, а не
          вторая полоса во всю ширину рядом с полосой дней. */}
      <span className={cn(CHIP, 'border-info-line bg-info-tint text-info-strong')}>
        <span className="type-small-medium">{phaseLabel}</span>
        {account.progress_to_next != null ? (
          <ProgressBar
            className="w-number"
            value={account.progress_to_next}
            max={1}
            label={phaseLabel}
          />
        ) : null}
      </span>
      {next != null ? (
        <span className="px-1">{t('warming.console.toNext', { days: next })}</span>
      ) : null}
      <span className={CHIP}>
        {t(`warming.persona.${account.activity_persona ?? 'normal'}.name`)}
      </span>
      <span className={cn(CHIP, trust != null ? trustTone(trust) : 'text-content-subtle')}>
        <Icon name="shield-check" size={12} className="shrink-0" />
        {t('warming.console.trust', { score: trust ?? '—' })}
      </span>
      <span
        className={cn(
          CHIP,
          spam === 'limited' && 'border-danger-line bg-danger-tint text-danger-deep',
        )}
      >
        {t(`warming.console.spam.${spam}`)}
      </span>
    </div>
  );
}

function dmText(t: TFunction, account: WarmingAccountState): string {
  const status = account.dm_status ?? 'on';
  if (status === 'on') return t('warming.console.dmOn');
  return t('warming.console.dmOff', { reason: t(`warming.console.dmReason.${status}`) });
}

// Row 4: «Сегодня 6 из 15 действий · следующий заход через 1:20 · ЛС: выкл., …».
// The count is what was settled, not the budget a running cycle has booked.
export function TodayRow({ account }: { account: WarmingAccountState }) {
  const { t } = useTranslation();
  const now = useMinuteClock();
  const done = account.daily_settled ?? 0;
  const cap = account.daily_cap ?? 0;
  const parts = [
    cap > 0
      ? t('warming.console.today', { done: Math.min(done, cap), cap })
      : t('warming.console.todayNoCap', { done }),
  ];
  const wait =
    account.state === 'active' && account.last_action ? null : countdown(account.next_run_at, now);
  if (wait) parts.push(t('warming.console.next', { time: wait }));
  parts.push(dmText(t, account));
  return <p className="m-0 mt-3 type-small text-content-muted">{parts.join(' · ')}</p>;
}

// The optional steps this card's stint runs, reading first: a phone card drops what a
// visit never does (joins, dialogues), and a step the settings switched off is left out.
const MECHANICS = ['join', 'reactions', 'stories', 'dialogue'] as const;

export function MechanicsRow({ account }: { account: WarmingAccountState }) {
  const { t } = useTranslation();
  // An absent value takes the schema default: dialogue off, the rest on.
  const on = MECHANICS.filter((step) => account.mechanics?.[step] ?? step !== 'dialogue');
  const list = ['read', ...on].map((step) => t(`warming.console.mechanic.${step}`)).join(' · ');
  return (
    <p className="m-0 mt-3 type-small text-content-muted">
      {t('warming.console.mechanics', { list })}
    </p>
  );
}

// While a cycle runs: which kind of visit it is and how far along its own step order
// (`cycle_plan`, item 4.4). The engine only ever moves `last_action` forward along
// that order, so the bar never jumps back.
export function VisitRow({ account }: { account: WarmingAccountState }) {
  const { t } = useTranslation();
  const plan = account.cycle_plan ?? [];
  if (account.state !== 'active' || !account.visit_kind || plan.length === 0) return null;
  const at = plan.findIndex((step) => step === account.last_action);
  const label = t('warming.visit.label', { kind: t(`warming.visit.kind.${account.visit_kind}`) });
  return (
    <div className="mt-3">
      <p className="m-0 mb-1 type-small text-content-muted">{label}</p>
      <ProgressBar value={Math.max(1, at + 1)} max={plan.length} label={label} />
    </div>
  );
}

function attentionText(t: TFunction, account: WarmingAccountState): string | null {
  const item = account.attention;
  if (!item) return null;
  const time = item.until ? formatLocalTime(item.until) : null;
  if (item.code === 'quarantine') {
    const label = t('warming.console.attention.quarantine', {
      attempt: item.attempt ?? 1,
      attempts: item.attempts ?? 1,
    });
    return time ? `${label} · ${t('warming.console.until', { time })}` : label;
  }
  if (item.code === 'flood_wait') {
    return t('warming.console.attention.flood_wait', { time: time ?? '—' });
  }
  if (item.code === 'not_ready') {
    const reasons = (item.reasons ?? [])
      .map((reason) => {
        const key = readinessReasonKey(reason);
        return key ? t(key) : reason;
      })
      .join(', ');
    return t('warming.console.attention.not_ready', { reasons });
  }
  return t(`warming.console.attention.${item.code}`);
}

// Row 5, only when something is wrong: «Карантин 2/3», «Флуд до 14:30», «Не готов: …».
export function AttentionRow({ account }: { account: WarmingAccountState }) {
  const { t } = useTranslation();
  const text = attentionText(t, account);
  if (!text) return null;
  return <CardAlert>{text}</CardAlert>;
}

// Предупреждение внутри карточки: одна компактная строка на тонированной подложке, без
// рамки — рамкой тон уже говорит сама карточка.
export function CardAlert({
  tone = 'warning',
  icon = 'alert-triangle',
  children,
}: {
  tone?: 'warning' | 'info';
  icon?: IconName;
  children: ReactNode;
}) {
  return (
    <div
      role="status"
      className={cn(
        'mt-3 flex items-center gap-2 rounded-sm px-2 py-1 type-small',
        tone === 'warning' ? 'bg-warning-tint text-warning-deep' : 'bg-info-tint text-info-strong',
      )}
    >
      <Icon name={icon} size={14} className="shrink-0" />
      {children}
    </div>
  );
}
