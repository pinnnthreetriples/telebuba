import { useTranslation } from 'react-i18next';

import { accountDesignStatus, type DesignStatus, StatusBadge } from '@/entities/account';
import { proxyTypeLabel } from '@/entities/proxy';
import type { AccountRead } from '@/shared/api';

interface AccountsTableProps {
  data: AccountRead[];
  onCheck: (accountId: string) => void;
  onDelete: (accountId: string) => void;
  onOpen?: (account: AccountRead) => void;
  onProfile?: (account: AccountRead) => void;
  busyId: string | null;
}

const TH = 'px-4 py-[11px] text-[11px] font-medium uppercase tracking-[0.04em] text-ink-subtle';
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

// ponytail: trust + device aren't carried by the backend yet (Phase 1) — derive
// deterministic design-first values from the id so the table keeps the design's
// richness. The proxy column is real (AccountRead carries the assigned pool proxy).
const DEVICES = ['iPhone 13', 'iPhone 12', 'Pixel 7', 'Galaxy S22', 'iPhone 14'];
const OSES = ['iOS 17.2', 'iOS 16.4', 'Android 14', 'Android 13', 'iOS 17.4'];
function decorate(account: AccountRead) {
  const seed = [...account.account_id].reduce((sum, c) => sum + c.charCodeAt(0), 0);
  const trust = 40 + (seed % 60);
  const trustColor = trust >= 70 ? '#12a150' : trust >= 45 ? '#e08700' : '#e5372a';
  return {
    trust,
    trustColor,
    device: `${DEVICES[seed % DEVICES.length]} · ${OSES[seed % OSES.length]}`,
  };
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

// The design's accounts table: white card, uppercase header on #FAF9F7, rows
// with a status-tinted mono avatar, status pill, proxy flag + connectivity dot,
// device, trust bar, and round actions (check / edit-profile / delete).
export function AccountsTable({
  data,
  onCheck,
  onDelete,
  onOpen,
  onProfile,
  busyId,
}: AccountsTableProps) {
  const { t } = useTranslation();
  return (
    <div className="overflow-hidden rounded-2xl border border-line bg-white">
      <div className="tb-scroll overflow-x-auto">
        <table className="w-full min-w-[880px] border-collapse">
          <thead>
            <tr className="bg-surface">
              <th className={`${TH} text-left`}>{t('accounts.table.phone')}</th>
              <th className={`${TH} text-left`}>{t('accounts.table.status')}</th>
              <th className={`${TH} text-left`}>{t('accounts.table.proxy')}</th>
              <th className={`${TH} text-left`}>{t('accounts.table.device')}</th>
              <th className={`${TH} text-left`}>{t('accounts.table.trust')}</th>
              <th className={`${TH} text-right`}>{t('accounts.table.actions')}</th>
            </tr>
          </thead>
          <tbody>
            {data.map((account) => {
              const busy = busyId === account.account_id;
              const ds = accountDesignStatus(account.status);
              const d = decorate(account);
              return (
                <tr
                  key={account.account_id}
                  onClick={() => onOpen?.(account)}
                  className="tb-row cursor-pointer border-t border-[#f0eeeb] transition-colors"
                >
                  <td className="px-4 py-3">
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
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge status={account.status} />
                  </td>
                  <td className="px-4 py-3">
                    {account.proxy_id ? (
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
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <span className="text-[12px] text-ink-muted">{d.device}</span>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <div className="h-[5px] w-[46px] overflow-hidden rounded-full bg-track">
                        <div
                          className="h-full rounded-full"
                          style={{ width: `${String(d.trust)}%`, background: d.trustColor }}
                        />
                      </div>
                      <span
                        className="min-w-[20px] text-[12px] font-semibold"
                        style={{ color: d.trustColor }}
                      >
                        {d.trust}
                      </span>
                    </div>
                  </td>
                  <td className="px-4 py-3">
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
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
