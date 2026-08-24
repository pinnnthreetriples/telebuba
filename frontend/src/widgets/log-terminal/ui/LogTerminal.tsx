import type { TFunction } from 'i18next';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { LogEntry } from '@/shared/api';
import { eventLabel, eventReason, formatLocalTime, logSeverity } from '@/shared/lib';
import { CollapsibleCard, Icon, IconButton } from '@/shared/ui';

// Activity-feed line tone by the event's display severity (see `logSeverity`). The
// dark-surface tokens, shared with the warming card's log — three parallel triples
// of on-dark green/amber/red existed before.
const LOG_TONE: Record<'success' | 'warning' | 'error', string> = {
  success: 'text-term-success',
  warning: 'text-term-warning',
  error: 'text-term-error',
};

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
  const detail = eventReason(t, line);
  const hint = t(`logEventHint.${line.event}`, { defaultValue: '' });
  return (
    <div className="flex gap-md" title={hint || undefined}>
      <span className="shrink-0 text-term-dim">
        {formatLocalTime(line.created_at, { seconds: true })}
      </span>
      {/* Clicking a name narrows the feed to it — the point of the column is following
          ONE account through a burst, which reading alone can't do at 80 rows. Listener /
          sweep rows have no account and render a blank spacer instead of an empty
          <button> (no accessible name), so the columns after it still line up. */}
      {accountId ? (
        <button
          type="button"
          title={t('logTerminal.filterByAccount')}
          onClick={() => {
            onPickAccount(accountId);
          }}
          className="w-[110px] shrink-0 truncate text-left text-term-text hover:text-white hover:underline"
        >
          {account}
        </button>
      ) : (
        <span className="w-[110px] shrink-0" />
      )}
      {channel ? <span className="shrink-0 text-term-link">{channel}</span> : null}
      <span className={LOG_TONE[logSeverity(line)]}>{eventLabel(t, line.event)}</span>
      {detail ? <span className="truncate text-term-dim">· {detail}</span> : null}
    </div>
  );
}

/** The activity terminal — the tail of a live log stream, in a collapsible card.
 *
 * A widget rather than a `shared/ui` primitive, and not beside the neurocomment
 * card it started in. Both halves of that are forced:
 *
 * - `fsd/forbidden-imports` bars page→page, so the neuroshilling launch card
 *   could otherwise only have had a second copy of it;
 * - it reads `shared/lib`'s log vocabulary (`eventLabel`, `eventReason`,
 *   `logSeverity`), and `shared/lib/query-client` imports `shared/ui` — putting
 *   this in the `shared/ui` barrel closed that into an import cycle that left
 *   `toastError` undefined at module init. A component that understands log rows
 *   is not a generic primitive anyway.
 *
 * `title` is the one thing the two callers differ on; the strings below it are
 * generic and moved to `logTerminal.*` with the component.
 */
export function LogTerminal({
  title,
  logLines,
  onClear,
  accountName,
}: {
  title: string;
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
      label={title}
      headerClassName="px-lg py-lg"
      bodyClassName="px-lg pb-lg"
      trailing={
        <>
          {onlyAccount ? (
            // In `trailing`, not `header`: CollapsibleCard wraps `header` in its own
            // toggle <button>, and a nested button is invalid HTML. Sits in the head
            // row either way, so it stays visible while the rows scroll — otherwise a
            // filter you scrolled past just looks like an empty log.
            <button
              type="button"
              title={t('logTerminal.showAll')}
              onClick={() => {
                setOnlyAccount(null);
              }}
              className="rounded-full bg-primary-tint px-sm py-hair text-tiny font-medium text-primary-deep hover:bg-danger-line hover:text-danger"
            >
              {t('logTerminal.filteredBy', {
                name: accountName?.(onlyAccount) ?? onlyAccount,
              })}
            </button>
          ) : null}
          {onClear && logLines.length > 0 ? (
            <IconButton
              size="md"
              tone="danger"
              aria-label={t('logTerminal.clear')}
              title={t('logTerminal.clear')}
              onClick={onClear}
            >
              <Icon name="trash" size={16} />
            </IconButton>
          ) : null}
        </>
      }
      header={
        <>
          <span className="pl-pulse h-[7px] w-[7px] shrink-0 rounded-full bg-primary" />
          <span className="text-lead font-semibold">{title}</span>
          <span className="rounded-full bg-track px-sm py-hair text-tiny font-medium text-ink-muted">
            {shown.length}
          </span>
        </>
      }
    >
      <div className="term tb-scroll max-h-[220px] overflow-y-auto rounded-lg bg-term px-lg py-md font-mono text-tiny leading-[1.85]">
        {shown.length === 0 ? (
          <div className="text-term-dim">{t('logTerminal.empty')}</div>
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
