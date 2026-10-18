import type { TFunction } from 'i18next';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { WarmingAccountState } from '@/shared/api';
import { Icon, type IconName } from '@/shared/ui';

import { countdown, MINUTE_MS } from '../../lib/countdown';

// The card's facts as one quiet tile of label → value pairs, the way a fitness card
// shows "Duration 30m · Energy 808": when the account comes back, whether it may write
// DMs, what @SpamBot said, and its persona. Colour only tints the value; the words carry
// the meaning.
type Tone = 'ok' | 'warn' | 'bad' | 'plain';

const TONE: Record<Tone, string> = {
  ok: 'text-success-deep',
  warn: 'text-warning-deep',
  bad: 'text-danger',
  plain: 'text-content-primary',
};

interface Fact {
  icon: IconName;
  label: string;
  value: string;
  note?: string;
  tone: Tone;
}

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

function visitFact(t: TFunction, account: WarmingAccountState, now: number): Fact {
  const label = t('warming.card.fact.visit');
  if (account.state === 'active') {
    return { icon: 'play', label, value: t('warming.card.liveNow'), tone: 'ok' };
  }
  const wait = countdown(account.next_run_at, now);
  return {
    icon: 'clock',
    label,
    value: wait ? t('warming.card.inTime', { time: wait }) : '—',
    tone: 'plain',
  };
}

function dmFact(t: TFunction, account: WarmingAccountState): Fact {
  const status = account.dm_status ?? 'on';
  const label = t('warming.card.fact.dm');
  if (status === 'on') return { icon: 'send', label, value: t('warming.card.on'), tone: 'ok' };
  return {
    icon: 'send',
    label,
    value: t('warming.card.off'),
    note: t(`warming.console.dmReason.${status}`),
    tone: status === 'paused' ? 'warn' : 'plain',
  };
}

function spamFact(t: TFunction, account: WarmingAccountState): Fact {
  const status =
    account.spam_status === 'clean' || account.spam_status === 'limited'
      ? account.spam_status
      : 'unknown';
  const tone: Tone = status === 'clean' ? 'ok' : status === 'limited' ? 'bad' : 'plain';
  return {
    icon: 'shield-check',
    label: t('warming.card.fact.spam'),
    value: t(`warming.card.spam.${status}`),
    tone,
  };
}

export function CardChips({ account }: { account: WarmingAccountState }) {
  const { t } = useTranslation();
  const now = useMinuteClock();
  const facts: Fact[] = [
    visitFact(t, account, now),
    dmFact(t, account),
    spamFact(t, account),
    {
      icon: 'user-round',
      label: t('warming.card.fact.persona'),
      value: t(`warming.persona.${account.activity_persona ?? 'normal'}.name`),
      tone: 'plain',
    },
  ];
  return (
    <dl className="m-0 grid grid-cols-2 gap-x-4 gap-y-3 rounded-md bg-canvas p-4">
      {facts.map((fact) => (
        <div key={fact.label} className="min-w-0">
          <dt className="flex items-center gap-1 type-small text-content-muted">
            <Icon name={fact.icon} size={12} className="shrink-0" />
            {fact.label}
          </dt>
          <dd className={`m-0 truncate type-body-medium ${TONE[fact.tone]}`}>{fact.value}</dd>
          {fact.note ? (
            <dd className="m-0 truncate type-small text-content-subtle" title={fact.note}>
              {fact.note}
            </dd>
          ) : null}
        </div>
      ))}
    </dl>
  );
}
