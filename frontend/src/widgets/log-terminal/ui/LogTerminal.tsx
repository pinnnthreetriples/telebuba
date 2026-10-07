import type { TFunction } from 'i18next';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { AccountAvatar, accountDisplayName } from '@/entities/account';
import type { LogEntry } from '@/shared/api';
import { FOCUS_RING } from '@/shared/design-system';
import { eventLabel, eventReason, formatLocalTime, logSeverity } from '@/shared/lib';
import { Badge, Button, CollapsibleCard, Icon, IconButton, TerminalPane } from '@/shared/ui';

// Activity-feed line tone by the event's display severity (see `logSeverity`). The
// dark-surface tokens, shared with the warming card's log — three parallel triples
// of on-dark green/amber/red existed before.
const LOG_TONE: Record<'success' | 'warning' | 'error', string> = {
  success: 'text-term-success',
  warning: 'text-term-warning',
  error: 'text-term-error',
};

/** What a journal line needs to know about its account: the avatar's fields and a name.
 * Structural, so callers pass their fleet's `AccountRead` as is. */
type LogAccount = Parameters<typeof AccountAvatar>[0]['account'] & {
  username?: string | null;
};

// The one name an account goes by in the journal — the avatar's hover and its accessible
// name: the Telegram display name, with the @handle beside it when there is one.
function logAccountName(account: LogAccount): string {
  const name = accountDisplayName(account);
  return account.username ? `${name} · @${account.username}` : name;
}

function extraStr(extra: LogEntry['extra'], key: string): string | undefined {
  const value = extra?.[key];
  return typeof value === 'string' ? value : undefined;
}

// One terminal line: time · account · channel · event · reason, with a hover hint (why + fix).
function LogLine({
  line,
  t,
  accountOf,
  onPickAccount,
}: {
  line: LogEntry;
  t: TFunction;
  accountOf?: (accountId: string) => LogAccount | undefined;
  onPickAccount: (accountId: string) => void;
}) {
  const channel = extraStr(line.extra, 'channel');
  // Who did it — a burst of identical rows is unattributable without this. Rows
  // with no account_id (listener / sweep) leave the column empty, like `channel`.
  const accountId = line.account_id;
  const account = accountId ? accountOf?.(accountId) : undefined;
  const detail = eventReason(t, line);
  const hint = t(`logEventHint.${line.event}`, { defaultValue: '' });
  return (
    <div className="flex items-center gap-1" title={hint || undefined}>
      <span className="shrink-0 text-term-dim">
        {formatLocalTime(line.created_at, { seconds: true })}
      </span>
      {/* Clicking an account narrows the feed to it — the point of the column is
          following ONE account through a burst, which reading alone can't do at 80 rows.
          A known account is its round face (photo or initials), named on hover and to a
          screen reader; one the fleet no longer has (deleted) is a neutral face of the same
          size, named by its id. Listener / sweep rows have no account and render a blank
          spacer of the face's size instead of an empty <button> (no accessible name), so
          the columns after it line up whatever mix of the three a burst holds. */}
      {accountId && account ? (
        <button
          type="button"
          aria-label={logAccountName(account)}
          title={logAccountName(account)}
          onClick={() => {
            onPickAccount(accountId);
          }}
          className={`mr-1 inline-flex shrink-0 rounded-full ${FOCUS_RING}`}
        >
          <AccountAvatar
            account={account}
            className="size-glyph rounded-full"
            fallbackClassName="bg-info-tint text-small font-medium text-info-strong"
          />
        </button>
      ) : accountId ? (
        <button
          type="button"
          aria-label={accountId}
          title={`${accountId} · ${t('logTerminal.filterByAccount')}`}
          onClick={() => {
            onPickAccount(accountId);
          }}
          className={`mr-1 inline-flex size-glyph shrink-0 items-center justify-center rounded-full bg-term-thumb text-term-text hover:text-on-fill ${FOCUS_RING}`}
        >
          <Icon name="user-round" size={10} />
        </button>
      ) : (
        <span className="mr-1 size-glyph shrink-0" />
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
  accountOf,
}: {
  title: string;
  logLines: LogEntry[];
  onClear?: () => void;
  /** The caller's fleet lookup; an id it does not know shows as text. */
  accountOf?: (accountId: string) => LogAccount | undefined;
}) {
  const { t } = useTranslation();
  // Which account the feed is narrowed to, or null for everything. Card-local on
  // purpose: it is a reading aid over the rows already streamed in, not a query —
  // the stream keeps delivering every account, this only hides the rest.
  const [onlyAccount, setOnlyAccount] = useState<string | null>(null);
  const shown = onlyAccount ? logLines.filter((l) => l.account_id === onlyAccount) : logLines;
  const filteredAccount = onlyAccount ? accountOf?.(onlyAccount) : undefined;
  const filteredName = filteredAccount ? accountDisplayName(filteredAccount) : onlyAccount;
  return (
    <CollapsibleCard
      defaultOpen
      label={title}
      headerClassName="px-4 py-4"
      bodyClassName="px-4 pb-4"
      trailing={
        <>
          {onlyAccount ? (
            // In `trailing`, not the heading: CollapsibleCard wraps its heading in its own
            // toggle <button>, and a nested button is invalid HTML. Sits in the head
            // row either way, so it stays visible while the rows scroll — otherwise a
            // filter you scrolled past just looks like an empty log.
            <Button
              variant="ghost"
              size="sm"
              title={t('logTerminal.showAll')}
              onClick={() => {
                setOnlyAccount(null);
              }}
              className="bg-info-tint text-info-strong hover:bg-danger-tint hover:text-danger-deep"
            >
              {t('logTerminal.filteredBy', { name: filteredName })}
            </Button>
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
      dot="active"
      title={title}
      badge={<Badge tone="neutral">{shown.length}</Badge>}
    >
      <TerminalPane>
        {shown.length === 0 ? (
          <div className="text-term-dim">{t('logTerminal.empty')}</div>
        ) : (
          shown.map((line) => (
            <LogLine
              key={line.id}
              line={line}
              t={t}
              accountOf={accountOf}
              onPickAccount={setOnlyAccount}
            />
          ))
        )}
      </TerminalPane>
    </CollapsibleCard>
  );
}
