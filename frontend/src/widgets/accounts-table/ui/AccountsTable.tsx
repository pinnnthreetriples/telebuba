import { type ColumnDef } from '@tanstack/react-table';
import { useTranslation } from 'react-i18next';

import {
  AccountAvatar,
  accountDesignStatus,
  accountDisplayName,
  type DesignStatus,
  StatusBadge,
} from '@/entities/account';
import { proxyTypeLabel } from '@/entities/proxy';
import type { AccountRead } from '@/shared/api';
import type { FeedbackResult } from '@/shared/lib';
import { Card, DataTable, type DataTableColumnMeta, Icon, StatusIcon } from '@/shared/ui';

interface AccountsTableProps {
  data: AccountRead[];
  onCheck: (accountId: string) => void;
  onDelete: (accountId: string) => void;
  onOpen?: (account: AccountRead) => void;
  onProfile?: (account: AccountRead) => void;
  // A set, not one id: two rows can have a check or a delete in flight at once.
  busyIds: ReadonlySet<string>;
  // Verdict of the row's last check, while it is being flashed. Absent = the
  // button shows its ordinary refresh glyph.
  checkResults: Readonly<Record<string, FeedbackResult>>;
}

const ACTION_BTN =
  'flex size-icon items-center justify-center rounded-full border border-line bg-white disabled:opacity-50';

// The check button wears its own verdict (repo rule: every mutation ends in a
// green check or a red cross), so the ✓/✗ lands where the click did. A busy row
// falls back to `idle`: the button re-enables the moment its spinner clears, so
// a second click inside the flash window would otherwise spin on the previous
// verdict's fill — the old answer asserted over an unresolved check.
const CHECK_BTN: Record<FeedbackResult | 'idle', string> = {
  idle: 'text-ink-muted',
  ok: 'border-success bg-success-deep text-white',
  err: 'border-danger bg-danger text-white',
};

// The design's mono avatar tint per status (monoMap).
const AVATAR_CLASS: Record<DesignStatus, string> = {
  active: 'bg-primary-tint text-primary-deep',
  spam: 'bg-warning-tint text-warning-deep',
  code: 'bg-canvas text-ink-muted',
  banned: 'bg-danger-tint text-danger-deep',
};

// Row avatar: the shared account avatar (cached Telegram photo, else initials),
// with the status-tinted fallback the design specifies for this table.
function RowAvatar({ account }: { account: AccountRead }) {
  const ds = accountDesignStatus(account.status);
  return (
    <AccountAvatar
      account={account}
      className="size-tile shrink-0 rounded-full"
      fallbackClassName={`text-body font-semibold ${AVATAR_CLASS[ds]}`}
    />
  );
}

// Trust Score is real (computed by the backend from session/spam/age signals).
// The 3-tier band mirrors the design's thresholds, as text tokens: the bar takes
// `bg-current` off the same class, so bar and number cannot disagree.
function trustTone(score: number): string {
  return score >= 70 ? 'text-success-deep' : score >= 45 ? 'text-warning-deep' : 'text-danger';
}

// Real device fingerprint — immutable, set at registration.
function deviceLabel(account: AccountRead): string {
  return [account.device_model, account.device_system_version].filter(Boolean).join(' · ') || '—';
}

// Real proxy column, sourced from the account's assigned pool proxy. Dot tone is
// the token the connectivity state means, not a hex of its own.
function proxyDotTone(status: string | null | undefined): string {
  if (status === 'tcp_working') return 'bg-success';
  if (status === 'failed') return 'bg-danger';
  return 'bg-line-strong';
}
function proxyMeta(account: AccountRead): string {
  return [
    account.proxy_country_code?.toUpperCase(),
    account.proxy_type ? proxyTypeLabel(account.proxy_type) : null,
  ]
    .filter(Boolean)
    .join(' · ');
}

const RIGHT_META: DataTableColumnMeta = { className: 'text-right', cellClassName: 'text-right' };
const LEFT_META: DataTableColumnMeta = { className: 'text-left' };

// The design's accounts table: white card, uppercase header on #FAF9F7, rows
// with a status-tinted mono avatar, status pill, proxy flag + connectivity dot,
// device, trust bar, and round actions (check / edit-profile / delete). Built on
// @tanstack/react-table via the shared DataTable so later clusters share one shell.
export function AccountsTable({
  data,
  onCheck,
  onDelete,
  onOpen,
  onProfile,
  busyIds,
  checkResults,
}: AccountsTableProps) {
  const { t } = useTranslation();

  // Not memoized: the page passes two of the four callbacks as inline arrows, so
  // those alone are a fresh identity on every parent render and a useMemo here
  // could never hit — it would only claim otherwise. (`busyIds` is useState, so
  // its identity IS stable between the renders that do not touch the set; the
  // inline arrows are what settles this.)
  const columns: ColumnDef<AccountRead>[] = [
    {
      id: 'phone',
      header: () => t('accounts.table.phone'),
      // Spread rather than editing LEFT_META itself — five columns share it.
      meta: { ...LEFT_META, cardSlot: 'title' } satisfies DataTableColumnMeta,
      cell: ({ row }) => {
        const account = row.original;
        return (
          <div className="flex items-center gap-md">
            <RowAvatar account={account} />
            <div>
              <div className="text-lead font-semibold">{accountDisplayName(account)}</div>
              <div className="text-tiny text-ink-subtle">
                {account.username ? `@${account.username}` : '—'}
              </div>
            </div>
          </div>
        );
      },
    },
    {
      id: 'status',
      header: () => t('accounts.table.status'),
      meta: LEFT_META,
      cell: ({ row }) => <StatusBadge status={row.original.status} />,
    },
    {
      id: 'proxy',
      header: () => t('accounts.table.proxy'),
      meta: LEFT_META,
      cell: ({ row }) => {
        const account = row.original;
        return account.proxy_id ? (
          <div className="flex items-center gap-sm">
            <span
              className={`size-dot shrink-0 rounded-full ${proxyDotTone(account.proxy_status)}`}
            />
            {account.proxy_country_code ? (
              <span
                className={`fi fi-${account.proxy_country_code.toLowerCase()} h-flag w-flag rounded-[2px] shadow-ring`}
              />
            ) : null}
            <span className="text-body text-ink-body">{proxyMeta(account)}</span>
          </div>
        ) : (
          <span className="text-body text-ink-subtle">—</span>
        );
      },
    },
    {
      id: 'device',
      header: () => t('accounts.table.device'),
      meta: LEFT_META,
      cell: ({ row }) => (
        <span className="text-body text-ink-muted">{deviceLabel(row.original)}</span>
      ),
    },
    {
      id: 'trust',
      header: () => t('accounts.table.trust'),
      meta: LEFT_META,
      cell: ({ row }) => {
        const trust = row.original.trust_score;
        return trust == null ? (
          <span className="text-body text-ink-subtle">—</span>
        ) : (
          <div className="flex items-center gap-sm">
            <div
              // eslint-disable-next-line design-tokens/no-raw-values -- see the note in the rule: the trust bar's own length inside one cell
              className="h-meter w-[46px] overflow-hidden rounded-full bg-canvas"
            >
              <div
                className={`h-full rounded-full bg-current ${trustTone(trust)}`}
                style={{ width: `${String(trust)}%` }}
              />
            </div>
            <span className={`min-w-badge text-body font-semibold ${trustTone(trust)}`}>
              {trust}
            </span>
          </div>
        );
      },
    },
    {
      id: 'actions',
      header: () => t('accounts.table.actions'),
      meta: { ...RIGHT_META, cardSlot: 'control' } satisfies DataTableColumnMeta,
      cell: ({ row }) => {
        const account = row.original;
        const busy = busyIds.has(account.account_id);
        const checked = checkResults[account.account_id];
        return (
          <div className="flex items-center justify-end gap-sm">
            <button
              type="button"
              title={t('accounts.actions.check')}
              disabled={busy}
              onClick={(event) => {
                event.stopPropagation();
                onCheck(account.account_id);
              }}
              className={`${ACTION_BTN} transition-colors duration-enter ${CHECK_BTN[busy ? 'idle' : (checked ?? 'idle')]}`}
            >
              {busy ? (
                <span className="tb-spin inline-block size-spinner rounded-full border-2 border-line-strong border-t-primary" />
              ) : checked ? (
                // Named, not colour-only: the fill and the glyph say nothing to a
                // screen reader, and `title` stays the constant action label.
                <span
                  className="tb-pop inline-flex"
                  role="img"
                  aria-label={t(
                    checked === 'ok' ? 'accounts.edit.aliveOk' : 'accounts.edit.aliveErr',
                  )}
                >
                  <StatusIcon kind={checked} />
                </span>
              ) : (
                <Icon name="refresh" size={14} />
              )}
            </button>
            <button
              type="button"
              title={t('accounts.actions.profile')}
              onClick={(event) => {
                event.stopPropagation();
                (onProfile ?? onOpen)?.(account);
              }}
              className={`${ACTION_BTN} text-ink-muted hover:border-primary-line hover:text-primary`}
            >
              <Icon name="pencil" size={14} />
            </button>
            <button
              type="button"
              title={t('accounts.actions.delete')}
              disabled={busy}
              onClick={(event) => {
                event.stopPropagation();
                onDelete(account.account_id);
              }}
              className={`${ACTION_BTN} text-ink-subtle hover:border-danger-line hover:text-danger`}
            >
              <Icon name="trash" size={14} />
            </button>
          </div>
        );
      },
    },
  ];

  return (
    <Card className="overflow-hidden">
      <div className="tb-scroll overflow-x-auto">
        <DataTable
          data={data}
          columns={columns}
          // The row IS the only way into the account-edit view (the pencil opens
          // the profile modal instead), so it has to be focusable and operable
          // from the keyboard — otherwise session, proxy, device, signals and the
          // actions card are unreachable without a mouse. Kept here rather than in
          // DataTable so the shared table stays generic; no role="button", which
          // would strip the row's table semantics.
          getRowProps={(row) => ({
            onClick: () => onOpen?.(row.original),
            onKeyDown: (event) => {
              // Only the row itself: Enter on an action button inside it must not
              // also open the row.
              if (event.target !== event.currentTarget) return;
              if (event.key !== 'Enter' && event.key !== ' ') return;
              event.preventDefault();
              onOpen?.(row.original);
            },
            tabIndex: 0,
            className: 'cursor-pointer',
          })}
        />
      </div>
    </Card>
  );
}
