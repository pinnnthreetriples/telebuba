import { useTranslation } from 'react-i18next';

import { StatusBadge } from '@/entities/account';
import type { AccountRead } from '@/shared/api';

interface AccountsTableProps {
  data: AccountRead[];
  onCheck: (accountId: string) => void;
  onDelete: (accountId: string) => void;
  busyId: string | null;
}

const TH = 'px-4 py-[11px] text-[11px] font-medium uppercase tracking-[0.04em] text-ink-subtle';
const ACTION_BTN =
  'flex h-[30px] w-[30px] items-center justify-center rounded-full border border-line bg-white disabled:opacity-50';

// last two phone digits → the mono avatar initials, matching the design.
function mono(account: AccountRead): string {
  const digits = (account.phone ?? account.account_id).replace(/\D/g, '');
  return digits.slice(-2) || '#';
}

// The design's accounts table: white card, uppercase header on #FAF9F7, rows
// with a mono avatar, status pill, proxy flag, trust bar, and round actions.
export function AccountsTable({ data, onCheck, onDelete, busyId }: AccountsTableProps) {
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
              return (
                <tr
                  key={account.account_id}
                  className="tb-row border-t border-[#f0eeeb] transition-colors"
                >
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-[11px]">
                      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary-tint text-[12px] font-semibold text-primary">
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
                    <div className="flex items-center gap-[7px]">
                      {account.proxy_country_code ? (
                        <span
                          className={`fi fi-${account.proxy_country_code.toLowerCase()} h-3 w-4 rounded-[2px]`}
                        />
                      ) : null}
                      <span className="text-[12px] text-[#3a3a3a]">
                        {account.proxy_country_code?.toUpperCase() ?? '—'}
                      </span>
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <span className="text-[12px] text-ink-muted">—</span>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <div className="h-[5px] w-[46px] overflow-hidden rounded-full bg-track">
                        <div className="h-full w-0 rounded-full bg-success" />
                      </div>
                      <span className="min-w-[20px] text-[12px] font-semibold text-ink-subtle">
                        —
                      </span>
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center justify-end gap-[6px]">
                      <button
                        type="button"
                        title={t('accounts.actions.check')}
                        disabled={busy}
                        onClick={() => {
                          onCheck(account.account_id);
                        }}
                        className={`${ACTION_BTN} text-ink-muted hover:border-[#bfd6ff] hover:text-primary`}
                      >
                        {busy ? (
                          <span className="tb-spin inline-block h-[13px] w-[13px] rounded-full border-2 border-line border-t-primary" />
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
                        title={t('accounts.actions.delete')}
                        disabled={busy}
                        onClick={() => {
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
