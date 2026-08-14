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
import { DataTable, type DataTableColumnMeta, StatusIcon } from '@/shared/ui';

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
  'flex h-[30px] w-[30px] items-center justify-center rounded-full border border-line bg-white disabled:opacity-50';

// The check button wears its own verdict (repo rule: every mutation ends in a
// green check or a red cross), so the ✓/✗ lands where the click did.
const CHECK_BTN: Record<FeedbackResult | 'idle', string> = {
  idle: 'text-ink-muted',
  ok: 'border-success bg-success text-white',
  err: 'border-danger bg-danger text-white',
};

// The design's mono avatar tint per status (monoMap).
const AVATAR_CLASS: Record<DesignStatus, string> = {
  active: 'bg-[#e8f0ff] text-[#0066ff]',
  spam: 'bg-[#fbf3e2] text-[#9a7b22]',
  code: 'bg-[#edebe7] text-[#74726e]',
  banned: 'bg-[#fbecec] text-[#c0473f]',
};

// Row avatar: the shared account avatar (cached Telegram photo, else initials),
// with the status-tinted fallback the design specifies for this table.
function RowAvatar({ account }: { account: AccountRead }) {
  const ds = accountDesignStatus(account.status);
  return (
    <AccountAvatar
      account={account}
      className="h-8 w-8 shrink-0 rounded-full"
      fallbackClassName={`text-[12px] font-semibold ${AVATAR_CLASS[ds]}`}
    />
  );
}

// Trust Score is real (computed by the backend from session/spam/age signals).
// The 3-tier colour band mirrors the design's thresholds.
function trustColor(score: number): string {
  return score >= 70 ? '#12a150' : score >= 45 ? '#e08700' : '#e5372a';
}

// Real device fingerprint — immutable, set at registration.
function deviceLabel(account: AccountRead): string {
  return [account.device_model, account.device_system_version].filter(Boolean).join(' · ') || '—';
}

// Real proxy column, sourced from the account's assigned pool proxy.
function proxyDotColor(status: string | null | undefined): string {
  if (status === 'tcp_working') return '#2e9e64';
  if (status === 'failed') return '#c0473f';
  return '#c8c6c2';
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
          <div className="flex items-center gap-[11px]">
            <RowAvatar account={account} />
            <div>
              <div className="text-[13px] font-semibold">{accountDisplayName(account)}</div>
              <div className="text-[11px] text-ink-subtle">
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
          <div className="flex items-center gap-[7px]">
            <span
              className="h-[7px] w-[7px] shrink-0 rounded-full"
              style={{ background: proxyDotColor(account.proxy_status) }}
            />
            {account.proxy_country_code ? (
              <span
                className={`fi fi-${account.proxy_country_code.toLowerCase()} h-3 w-4 rounded-[2px] shadow-[0_0_0_1px_rgba(0,0,0,0.07)]`}
              />
            ) : null}
            <span className="text-[12px] text-[#3a3a3a]">{proxyMeta(account)}</span>
          </div>
        ) : (
          <span className="text-[12px] text-ink-subtle">—</span>
        );
      },
    },
    {
      id: 'device',
      header: () => t('accounts.table.device'),
      meta: LEFT_META,
      cell: ({ row }) => (
        <span className="text-[12px] text-ink-muted">{deviceLabel(row.original)}</span>
      ),
    },
    {
      id: 'trust',
      header: () => t('accounts.table.trust'),
      meta: LEFT_META,
      cell: ({ row }) => {
        const trust = row.original.trust_score;
        return trust == null ? (
          <span className="text-[12px] text-ink-subtle">—</span>
        ) : (
          <div className="flex items-center gap-2">
            <div className="h-[5px] w-[46px] overflow-hidden rounded-full bg-track">
              <div
                className="h-full rounded-full"
                style={{ width: `${String(trust)}%`, background: trustColor(trust) }}
              />
            </div>
            <span
              className="min-w-[20px] text-[12px] font-semibold"
              style={{ color: trustColor(trust) }}
            >
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
          <div className="flex items-center justify-end gap-[6px]">
            <button
              type="button"
              title={t('accounts.actions.check')}
              disabled={busy}
              onClick={(event) => {
                event.stopPropagation();
                onCheck(account.account_id);
              }}
              className={`${ACTION_BTN} transition-colors duration-300 ${CHECK_BTN[checked ?? 'idle']}`}
            >
              {busy ? (
                <span className="tb-spin inline-block h-[13px] w-[13px] rounded-full border-2 border-[#c8c6c2] border-t-primary" />
              ) : checked ? (
                <span className="tb-pop inline-flex">
                  <StatusIcon kind={checked} />
                </span>
              ) : (
                <svg
                  width="14"
                  height="14"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.9"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <path d="M21 12a9 9 0 1 1-6.2-8.6" />
                  <path d="M21 3v6h-6" />
                </svg>
              )}
            </button>
            <button
              type="button"
              title={t('accounts.actions.profile')}
              onClick={(event) => {
                event.stopPropagation();
                (onProfile ?? onOpen)?.(account);
              }}
              className={`${ACTION_BTN} text-ink-muted hover:border-[#bfd6ff] hover:text-primary`}
            >
              <svg
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M12 20h9" />
                <path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z" />
              </svg>
            </button>
            <button
              type="button"
              title={t('accounts.actions.delete')}
              disabled={busy}
              onClick={(event) => {
                event.stopPropagation();
                onDelete(account.account_id);
              }}
              className={`${ACTION_BTN} text-ink-subtle hover:border-[#f0c9c5] hover:text-danger`}
            >
              <svg
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.8"
              >
                <path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" />
              </svg>
            </button>
          </div>
        );
      },
    },
  ];

  return (
    <div className="overflow-hidden rounded-2xl border border-line bg-white">
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
    </div>
  );
}
