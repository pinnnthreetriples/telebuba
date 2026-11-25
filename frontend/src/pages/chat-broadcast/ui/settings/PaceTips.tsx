// A small moving picture and one line of advice under a pause setting, for operators who
// have never thought about pacing. Each picture replays when its value changes (`key`), and
// the advice carries a button that puts the recommended value into the draft — Save still
// decides. Motion stops by itself under prefers-reduced-motion (see app/styles/index.css).
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import type { IconName } from '@/shared/ui';
import { Button, Icon } from '@/shared/ui';

import type { Range } from '../../model/paceTips';
import {
  EVEN_JOIN_DELAY,
  EVEN_REST,
  evenPause,
  hourPlan,
  joinVerdict,
  restVerdict,
} from '../../model/paceTips';

type Tone = 'good' | 'warn' | 'info';

const TONE: Record<Tone, string> = {
  good: 'bg-success-tint text-success-deep',
  warn: 'bg-warning-tint text-warning-deep',
  info: 'bg-info-tint text-info-strong',
};
const HOUR = 3600;
// The whole picture assembles in about this long, however many dots it has.
const ASSEMBLE_SECONDS = 1.2;
const STEP_SECONDS = 0.35;
// A sleep shorter than this has no room for its label.
const LABELLED_SLEEP_MINUTES = 5;

const delay = (seconds: number) => ({ animationDelay: `${String(seconds)}s` });

function Shell({
  status,
  tone,
  advice,
  children,
}: {
  status: string;
  tone: Tone;
  advice?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="mb-2 rounded-md bg-canvas p-3">
      {children}
      <p className={`mt-2 rounded-sm px-2 py-1 type-small ${TONE[tone]}`}>{status}</p>
      {advice}
    </div>
  );
}

function Advice({ text, action, onApply }: { text: string; action: string; onApply: () => void }) {
  return (
    <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
      <span className="type-small">{text}</span>
      <Button size="sm" onClick={onApply}>
        {action}
      </Button>
    </div>
  );
}

function Steps({ steps }: { steps: { icon: IconName; label: string }[] }) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      {steps.map((step, index) => (
        <span key={step.label} className="flex items-center gap-2">
          {index === 0 ? null : (
            <span className="tb-pop text-content-muted" style={delay(index * STEP_SECONDS)}>
              <Icon name="arrow-right" size={12} />
            </span>
          )}
          <span
            className="tb-pop inline-flex items-center gap-1 rounded-full bg-surface px-2 py-1 type-small"
            style={delay(index * STEP_SECONDS)}
          >
            <Icon name={step.icon} size={12} />
            {step.label}
          </span>
        </span>
      ))}
    </div>
  );
}

// One hour of one account: a dot per message, and the sleep its hourly limit forces.
export function HourTip({
  betweenChats,
  perHour,
  onApply,
}: {
  betweenChats: Range;
  // `null` when the account limit is off.
  perHour: number | null;
  onApply: (value: Range) => void;
}) {
  const { t } = useTranslation();
  const plan = hourPlan(betweenChats, perHour);
  const cap = perHour ?? plan.sent;
  const status = t(`chatBroadcast.settings.pace.tips.hour.${plan.verdict}`, {
    sent: plan.sent,
    cap,
    busy: plan.busyMinutes,
    rest: plan.restMinutes,
  });
  const even = perHour === null ? null : evenPause(perHour);
  const step = ASSEMBLE_SECONDS / Math.max(plan.sends.length, 1);
  return (
    <Shell
      status={status}
      tone={plan.verdict === 'burst' ? 'warn' : plan.verdict === 'even' ? 'good' : 'info'}
      advice={
        even === null || plan.verdict === 'even' ? null : (
          <Advice
            text={t('chatBroadcast.settings.pace.tips.hour.advice', even)}
            action={t('chatBroadcast.settings.pace.tips.apply')}
            onApply={() => {
              onApply(even);
            }}
          />
        )
      }
    >
      <p className="type-small">
        {perHour === null
          ? t('chatBroadcast.settings.pace.tips.hour.leadFree')
          : t('chatBroadcast.settings.pace.tips.hour.lead', { cap })}
      </p>
      <div
        key={`${String(plan.sent)}-${String(plan.restMinutes)}`}
        role="img"
        aria-label={status}
        className="relative mt-2 flex h-bar items-center overflow-hidden rounded-sm bg-surface"
      >
        {plan.sends.map((at, index) => (
          <span
            key={at}
            className="tb-pop absolute size-dot rounded-full bg-action-primary"
            style={{ left: `${String((at / HOUR) * 100)}%`, ...delay(index * step) }}
          />
        ))}
        {plan.restMinutes > 0 ? (
          <span
            className="tb-fadeup absolute inset-y-0 right-0 flex items-center justify-center bg-warning-tint type-small text-warning-deep"
            style={{
              width: `${String((plan.restMinutes / 60) * 100)}%`,
              ...delay(ASSEMBLE_SECONDS),
            }}
          >
            {plan.restMinutes >= LABELLED_SLEEP_MINUTES
              ? t('chatBroadcast.settings.pace.tips.hour.sleep', { minutes: plan.restMinutes })
              : null}
          </span>
        ) : null}
      </div>
      <div className="mt-1 flex justify-between type-small text-content-muted">
        <span>{t('chatBroadcast.settings.pace.tips.hour.from')}</span>
        <span>{t('chatBroadcast.settings.pace.tips.hour.to')}</span>
      </div>
    </Shell>
  );
}

// Into the chat, the wait, the first message.
export function JoinTip({
  minutes,
  onApply,
}: {
  minutes: number;
  onApply: (minutes: number) => void;
}) {
  const { t } = useTranslation();
  const verdict = joinVerdict(minutes);
  const wait =
    minutes === 0
      ? t('chatBroadcast.settings.pace.tips.join.noWait')
      : t('chatBroadcast.settings.pace.tips.join.waits', {
          wait: t(`chatBroadcast.settings.pace.delay${String(minutes)}`),
        });
  return (
    <Shell
      status={t(`chatBroadcast.settings.pace.tips.join.${verdict}`)}
      tone={verdict === 'good' ? 'good' : verdict === 'risky' ? 'warn' : 'info'}
      advice={
        verdict === 'good' ? null : (
          <Advice
            text={t('chatBroadcast.settings.pace.tips.join.advice')}
            action={t('chatBroadcast.settings.pace.tips.apply')}
            onApply={() => {
              onApply(EVEN_JOIN_DELAY);
            }}
          />
        )
      }
    >
      <div key={minutes}>
        <Steps
          steps={[
            { icon: 'user-plus', label: t('chatBroadcast.settings.pace.tips.join.entered') },
            { icon: 'clock', label: wait },
            { icon: 'send', label: t('chatBroadcast.settings.pace.tips.join.writes') },
          ]}
        />
      </div>
    </Shell>
  );
}

// One chat's rounds: the message, the rest, the same message again.
export function RestTip({
  minutes,
  onApply,
}: {
  minutes: Range;
  onApply: (minutes: Range) => void;
}) {
  const { t } = useTranslation();
  const verdict = restVerdict(minutes);
  return (
    <Shell
      status={t(`chatBroadcast.settings.pace.tips.rest.${verdict}`, { minutes: minutes.min })}
      tone={verdict === 'good' ? 'good' : 'warn'}
      advice={
        verdict === 'good' ? null : (
          <Advice
            text={t('chatBroadcast.settings.pace.tips.rest.advice')}
            action={t('chatBroadcast.settings.pace.tips.apply')}
            onApply={() => {
              onApply(EVEN_REST);
            }}
          />
        )
      }
    >
      <div key={`${String(minutes.min)}-${String(minutes.max)}`}>
        <Steps
          steps={[
            { icon: 'send', label: t('chatBroadcast.settings.pace.tips.rest.wrote') },
            { icon: 'pause', label: t('chatBroadcast.settings.pace.tips.rest.rests', minutes) },
            { icon: 'refresh', label: t('chatBroadcast.settings.pace.tips.rest.again') },
          ]}
        />
      </div>
    </Shell>
  );
}
