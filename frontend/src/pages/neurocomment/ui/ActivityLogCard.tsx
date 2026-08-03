import type { TFunction } from 'i18next';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { LogEntry } from '@/shared/api';
import { eventLabel, formatLocalTime, logSeverity } from '@/shared/lib';
import { CollapsibleCard } from '@/shared/ui';

// Activity-feed line colour by the event's display severity (see `logSeverity`).
const NEURO_LOG_COLOR: Record<'success' | 'warning' | 'error', string> = {
  success: '#7be0a6',
  warning: '#ffd27f',
  error: '#e5736b',
};

// Where a label for `extra.error_type` may live. `accounts.*.code.*` are the gateway's
// own stable codes (`session_dead`, `chat_admin_required`) — gateway-wide vocabulary that
// sits under an `accounts` key only because the accounts page was the first screen to
// translate them, and the gateway now logs them here too. Copying those 36 strings into a
// log-private namespace would buy nothing but two wordings of the same refusal.
const ERROR_LABEL_PREFIXES = [
  'logEventTelegram.error',
  'accounts.profile.code',
  'accounts.channel.code',
];

function extraStr(extra: LogEntry['extra'], key: string): string | undefined {
  const value = extra?.[key];
  return typeof value === 'string' ? value : undefined;
}

// One terminal line: time · account · channel · event · reason, with a hover hint (why + fix).
function LogLine({
  line,
  t,
  accountName,
  onPickAccount,
}: {
  line: LogEntry;
  t: TFunction;
  accountName?: (accountId: string) => string;
  onPickAccount: (accountId: string) => void;
}) {
  const channel = extraStr(line.extra, 'channel');
  // Who did it — a burst of identical rows is unattributable without this. Rows
  // with no account_id (listener / sweep) leave the column empty, like `channel`.
  const accountId = line.account_id;
  const account = accountId ? (accountName?.(accountId) ?? accountId) : undefined;
  // Most negative outcomes carry a `reason`; a failed post carries the Telegram `status`.
  const reasonCode = extraStr(line.extra, 'reason') ?? extraStr(line.extra, 'status');
  const reason = reasonCode ? t(`logEventReason.${reasonCode}`, { defaultValue: '' }) : '';
  // What Telegram refused — an exception class or a gateway stable code. Shown NEXT TO
  // the reason rather than as a fallback behind it: `status: "failed"` always translates,
  // so a fallback would never fire and the line would keep saying a post failed without
  // saying why. Translated because this half IS the answer to "what went wrong", and
  // `ChannelPrivateError` next to the word "ошибка" says nothing twice; anything none of
  // the maps know still renders raw, like eventLabel's raw-event-code fallback.
  const errorType = extraStr(line.extra, 'error_type');
  // i18next resolves a key ARRAY to the first one that exists, so the ladder is its job,
  // not ours — and `defaultValue` then carries the raw value through untouched.
  const error = errorType
    ? t(
        ERROR_LABEL_PREFIXES.map((prefix) => `${prefix}.${errorType}`),
        { defaultValue: errorType },
      )
    : '';
  const detail = [reason, error].filter(Boolean).join(' · ');
  const hint = t(`logEventHint.${line.event}`, { defaultValue: '' });
  return (
    <div className="flex gap-[10px]" title={hint || undefined}>
      <span className="shrink-0 text-[#5c5c66]">
        {formatLocalTime(line.created_at, { seconds: true })}
      </span>
      {/* Clicking a name narrows the feed to it — the point of the column is following
          ONE account through a burst, which reading alone can't do at 80 rows. Listener /
          sweep rows have no account and render a blank spacer instead of an empty
          <button> (no accessible name), so the columns after it still line up. */}
      {accountId ? (
        <button
          type="button"
          title={t('neurocomment.log.filterByAccount')}
          onClick={() => {
            onPickAccount(accountId);
          }}
          className="w-[110px] shrink-0 truncate text-left text-[#c9c9d3] hover:text-white hover:underline"
        >
          {account}
        </button>
      ) : (
        <span className="w-[110px] shrink-0" />
      )}
      {channel ? <span className="shrink-0 text-[#6ea8fe]">{channel}</span> : null}
      <span style={{ color: NEURO_LOG_COLOR[logSeverity(line)] }}>{eventLabel(t, line.event)}</span>
      {detail ? <span className="truncate text-[#7a7a85]">· {detail}</span> : null}
    </div>
  );
}

// The neurocomment activity terminal — the tail of the live log stream.
export function ActivityLogCard({
  logLines,
  onClear,
  accountName,
}: {
  logLines: LogEntry[];
  onClear?: () => void;
  accountName?: (accountId: string) => string;
}) {
  const { t } = useTranslation();
  // Which account the feed is narrowed to, or null for everything. Card-local on
  // purpose: it is a reading aid over the rows already streamed in, not a query —
  // the stream keeps delivering every account, this only hides the rest.
  const [onlyAccount, setOnlyAccount] = useState<string | null>(null);
  const shown = onlyAccount ? logLines.filter((l) => l.account_id === onlyAccount) : logLines;
  return (
    <CollapsibleCard
      defaultOpen
      label={t('neurocomment.log.title')}
      headerClassName="px-4 py-[13px]"
      bodyClassName="px-[14px] pb-[14px]"
      trailing={
        <>
          {onlyAccount ? (
            // In `trailing`, not `header`: CollapsibleCard wraps `header` in its own
            // toggle <button>, and a nested button is invalid HTML. Sits in the head
            // row either way, so it stays visible while the rows scroll — otherwise a
            // filter you scrolled past just looks like an empty log.
            <button
              type="button"
              title={t('neurocomment.log.showAll')}
              onClick={() => {
                setOnlyAccount(null);
              }}
              className="rounded-full bg-primary-tint px-2 py-[2px] text-[11px] font-medium text-primary hover:bg-[#f0c9c5] hover:text-danger"
            >
              {t('neurocomment.log.filteredBy', {
                name: accountName?.(onlyAccount) ?? onlyAccount,
              })}
            </button>
          ) : null}
          {onClear && logLines.length > 0 ? (
            <button
              type="button"
              aria-label={t('neurocomment.log.clear')}
              title={t('neurocomment.log.clear')}
              onClick={onClear}
              className="flex h-[28px] w-[28px] items-center justify-center rounded-lg border border-line bg-white text-ink-subtle hover:border-[#f0c9c5] hover:bg-danger-tint hover:text-danger"
            >
              <svg
                width="15"
                height="15"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
              >
                <path d="M3 6h18" />
                <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
              </svg>
            </button>
          ) : null}
        </>
      }
      header={
        <>
          <span className="pl-pulse h-[7px] w-[7px] shrink-0 rounded-full bg-primary" />
          <span className="text-[13px] font-semibold">{t('neurocomment.log.title')}</span>
          <span className="rounded-full bg-[#f2f1ee] px-2 py-[2px] text-[11px] font-medium text-ink-muted">
            {shown.length}
          </span>
        </>
      }
    >
      <div className="term tb-scroll max-h-[220px] overflow-y-auto rounded-[10px] bg-[#16161a] px-[14px] py-3 font-mono text-[11px] leading-[1.85]">
        {shown.length === 0 ? (
          <div className="text-[#5c5c66]">{t('neurocomment.log.empty')}</div>
        ) : (
          shown.map((line) => (
            <LogLine
              key={line.id}
              line={line}
              t={t}
              accountName={accountName}
              onPickAccount={setOnlyAccount}
            />
          ))
        )}
      </div>
    </CollapsibleCard>
  );
}
