import { useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { Badge } from '@/shared/ui';

import type { AccountRead, SpamCheckResult } from '@/shared/api';
import { formatLocalDateTime, formatRelativeTo } from '@/shared/lib';

import { instantMs, spamVerdictOf, upcomingMs, VERDICT_DOT, VERDICT_TONE } from '../model/spamCard';
import { Section } from './_shared';
import { SpamAppealControls } from './SpamAppealControls';
import { SpamCheckControl } from './SpamCheckControl';
import { SpamCheckNotice } from './SpamCheckNotice';

interface Signal {
  dot: string;
  label: string;
  value: ReactNode;
}

// Spam/ban signals card: the folded verdict and where it came from, what the budget will do
// next, the platform restriction beside it (never inside it) and, for a limited account, the
// appeal the operator files by hand. Read-only apart from the @SpamBot check in the header.
export function SignalsSection({ account }: { account: AccountRead }) {
  const { t, i18n } = useTranslation();
  const [held, setHeld] = useState<SpamCheckResult | null>(null);
  const now = Date.now();
  const lang = i18n.language;

  const verdict = spamVerdictOf(account);
  const limited = verdict === 'limited';
  const confidence = account.spam_confidence;
  const observedMs = instantMs(account.spam_observed_at);
  const untilMs = instantMs(account.spam_valid_until);
  const nextMs = upcomingMs(account.spam_next_probe_at, now);

  const sourceName = account.spam_source
    ? t(`accounts.edit.spamCard.sourceName.${account.spam_source}`)
    : null;
  const sourceValue =
    sourceName === null
      ? null
      : account.spam_source === 'none' || observedMs === null
        ? sourceName
        : t('accounts.edit.spamCard.sourceLine', {
            source: sourceName,
            when: formatRelativeTo(observedMs, now, lang),
          });

  // The block row: the end of the limit when the backend dated it, "indefinitely" when it
  // did not, the stored detail for a payload that predates the verdict fields.
  let blockValue: string;
  if (verdict === 'frozen') blockValue = t('accounts.edit.spamCard.frozen');
  else if (!limited) blockValue = t('accounts.edit.signalNone');
  else if (untilMs !== null) {
    blockValue = t('accounts.edit.spamCard.limitUntil', {
      date: formatLocalDateTime(untilMs, lang),
    });
  } else if (account.spam_verdict) blockValue = t('accounts.edit.spamCard.limitForever');
  else blockValue = account.spam_detail ?? t('accounts.edit.signalRecorded');

  const signals: Signal[] = [
    {
      dot: VERDICT_DOT[verdict],
      label: t('accounts.edit.signalStatus'),
      value: (
        <span className="inline-flex flex-wrap items-center justify-end gap-2">
          <Badge tone={VERDICT_TONE[verdict]}>{t(`accounts.edit.spam.${verdict}`)}</Badge>
          {confidence ? (
            <span className="type-small text-content-subtle">
              {t('accounts.edit.spamCard.confidence', {
                level: t(`accounts.edit.spamCard.level.${confidence}`),
              })}
            </span>
          ) : null}
        </span>
      ),
    },
  ];
  if (sourceValue !== null) {
    signals.push({
      dot: 'bg-line-strong',
      label: t('accounts.edit.spamCard.source'),
      value: sourceValue,
    });
  }
  signals.push({
    dot: limited || verdict === 'frozen' ? VERDICT_DOT[verdict] : 'bg-line-strong',
    label: t('accounts.edit.signalBlock'),
    value: blockValue,
  });
  if (nextMs !== null) {
    signals.push({
      dot: 'bg-line-strong',
      label: t('accounts.edit.spamCard.nextCheck'),
      value: t('accounts.edit.spamCard.nextCheckAt', { when: formatLocalDateTime(nextMs, lang) }),
    });
  }
  if (account.spam_probes_7d != null) {
    signals.push({
      dot: 'bg-line-strong',
      label: t('accounts.edit.spamCard.probes7d'),
      value: String(account.spam_probes_7d),
    });
  }
  if (account.spam_tos_restricted) {
    signals.push({
      dot: 'bg-warning',
      label: t('accounts.edit.spamCard.tos'),
      value: t('accounts.edit.spamCard.tosValue', {
        reason: account.spam_tos_reason ?? t('accounts.edit.spamCard.tosNoReason'),
      }),
    });
  }
  signals.push({
    dot: account.last_checked_at ? 'bg-success' : 'bg-line-strong',
    label: t('accounts.edit.signalChecked'),
    value: account.last_checked_at
      ? account.last_checked_at.slice(0, 10)
      : t('accounts.edit.signalNever'),
  });

  return (
    <Section
      title={t('accounts.edit.signals')}
      right={<SpamCheckControl account={account} onResult={setHeld} />}
    >
      <div className="mb-2 type-body text-content-subtle">{t('accounts.edit.signalsReadonly')}</div>
      {held ? <SpamCheckNotice result={held} /> : null}
      <div className="flex flex-col">
        {signals.map((signal) => (
          <div
            key={signal.label}
            className="flex items-center justify-between gap-3 border-b border-canvas py-3"
          >
            <span className="flex items-center gap-2 type-body text-content-subtle">
              <span className={`size-dot shrink-0 rounded-full ${signal.dot}`} />
              {signal.label}
            </span>
            <span className="text-right type-body-medium text-content-primary">{signal.value}</span>
          </div>
        ))}
      </div>
      <SpamAppealControls account={account} />
    </Section>
  );
}
