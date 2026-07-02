import { type ColumnDef } from '@tanstack/react-table';
import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';

import { accountDesignStatus, type DesignStatus, StatusBadge } from '@/entities/account';
import { proxyTypeLabel } from '@/entities/proxy';
import type { AccountRead } from '@/shared/api';
import { DataTable, type DataTableColumnMeta } from '@/shared/ui';

interface AccountsTableProps {
  data: AccountRead[];
  onCheck: (accountId: string) => void;
  onDelete: (accountId: string) => void;
  onOpen?: (account: AccountRead) => void;
  onProfile?: (account: AccountRead) => void;
  busyId: string | null;
}

const ACTION_BTN =
  'flex h-[30px] w-[30px] items-center justify-center rounded-full border border-line bg-white disabled:opacity-50';

// The design's mono avatar tint per status (monoMap).
const AVATAR_CLASS: Record<DesignStatus, string> = {
  active: 'bg-[#e8f0ff] text-[#0066ff]',
  spam: 'bg-[#fbf3e2] text-[#9a7b22]',
  code: 'bg-[#edebe7] text-[#74726e]',
  banned: 'bg-[#fbecec] text-[#c0473f]',
};

// last two phone digits → the mono avatar initials, matching the design.
function mono(account: AccountRead): string {
  const digits = (account.phone ?? account.account_id).replace(/\D/g, '');
  return digits.slice(-2) || '#';
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
  busyId,
}: AccountsTableProps) {
  const { t } = useTranslation();

  const columns = useMemo<ColumnDef<AccountRead>[]>(
    () => [
      {
        id: 'phone',
        header: () => t('accounts.table.phone'),
        meta: LEFT_META,
        cell: ({ row }) => {
          const account = row.original;
          const ds = accountDesignStatus(account.status);
          return (
            <div className="flex items-center gap-[11px]">
              <div
                className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-[12px] font-semibold ${AVATAR_CLASS[ds]}`}
              >
                {mono(account)}
              </div>
              <div>
                <div className="text-[13px] font-semibold">
                  {account.phone ?? account.account_id}
                </div>
                <div className="text-[11px] text-ink-subtle">
                  {account.username ? `@${account.username}` : (account.label ?? '—')}
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
        meta: RIGHT_META,
        cell: ({ row }) => {
          const account = row.original;
          const busy = busyId === account.account_id;
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
                className={`${ACTION_BTN} text-ink-muted`}
              >
                {busy ? (
                  <span className="tb-spin inline-block h-[13px] w-[13px] rounded-full border-2 border-[#c8c6c2] border-t-primary" />
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
    ],
    [t, busyId, onCheck, onDelete, onOpen, onProfile],
  );

  return (
    <div className="overflow-hidden rounded-2xl border border-line bg-white">
      <div className="tb-scroll overflow-x-auto">
        <DataTable
          data={data}
          columns={columns}
          getRowProps={(row) => ({
            onClick: () => onOpen?.(row.original),
            className: 'cursor-pointer',
          })}
        />
      </div>
    </div>
  );
}
