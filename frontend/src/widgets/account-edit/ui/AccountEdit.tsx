import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { StatusBadge } from '@/entities/account';
import type { AccountRead } from '@/shared/api';

const FIELD =
  'tb-time w-full rounded-[10px] border border-line bg-white px-3 py-[9px] text-[13px] outline-none';
const LABEL = 'mb-[6px] block text-[12px] font-medium text-[#3a3a3a]';

function mono(account: AccountRead): string {
  return (account.phone ?? account.account_id).replace(/\D/g, '').slice(-2) || '#';
}

// The design's account-edit view (reached by clicking a row): header + session
// (state / code login / import) + proxy. ponytail: forms are mock until wired.
export function AccountEdit({ account, onBack }: { account: AccountRead; onBack: () => void }) {
  const { t } = useTranslation();
  const [importTab, setImportTab] = useState<'session' | 'tdata'>('session');
  const [proxyMode, setProxyMode] = useState<'pool' | 'manual'>('manual');
  const [showPass, setShowPass] = useState(false);
  const trust = 76;

  return (
    <div className="tb-fadeup max-w-[960px]">
      <button
        type="button"
        onClick={onBack}
        className="mb-4 inline-flex items-center gap-[6px] text-[13px] font-medium text-ink-muted hover:text-ink"
      >
        <svg
          width="15"
          height="15"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
        >
          <path d="m15 18-6-6 6-6" />
        </svg>
        {t('accounts.edit.back')}
      </button>

      <div className="mb-[14px] flex flex-wrap items-center gap-[18px] rounded-2xl border border-line bg-white px-5 py-[18px]">
        <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-primary-tint text-[17px] font-semibold text-primary">
          {mono(account)}
        </div>
        <div className="min-w-[150px] flex-1">
          <div className="text-[18px] font-bold">{account.phone ?? account.account_id}</div>
          <div className="text-[12px] text-ink-subtle">
            {account.username ? `@${account.username}` : (account.label ?? '—')}
          </div>
        </div>
        <StatusBadge status={account.status} />
        <div className="min-w-[130px]">
          <div className="flex items-center justify-end gap-2">
            <span className="text-[12px] text-ink-muted">{t('accounts.edit.trust')}</span>
            <span className="text-[18px] font-bold text-success">{trust}/100</span>
          </div>
          <div className="mt-[6px] h-[6px] overflow-hidden rounded-full bg-track">
            <div
              className="h-full rounded-full bg-success"
              style={{ width: `${String(trust)}%` }}
            />
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-[14px]">
        <div className="self-start rounded-2xl border border-line bg-white p-5">
          <div className="mb-4 text-[13px] font-semibold">{t('accounts.edit.session')}</div>
          <div className="mb-[10px] flex items-center justify-between gap-[10px] rounded-[10px] bg-[#f6f5f2] px-3 py-[10px]">
            <span className="flex items-center gap-2">
              <span className="h-2 w-2 rounded-full bg-success" />
              <span className="text-[12.5px] text-[#3a3a3a]">{t('accounts.edit.sessionOk')}</span>
            </span>
            <button
              type="button"
              className="rounded-[8px] border border-line bg-white px-3 py-[5px] text-[12px] font-medium text-ink-muted"
            >
              {t('accounts.edit.logout')}
            </button>
          </div>
          <div className="mb-[9px] mt-4 text-[11px] font-semibold uppercase tracking-[0.04em] text-ink-subtle">
            {t('accounts.edit.loginByCode')}
          </div>
          <div className="mb-[9px] grid grid-cols-2 gap-[10px]">
            <label>
              <span className={LABEL}>{t('accounts.edit.smsCode')}</span>
              <input placeholder="1 2 3 4 5" className={`${FIELD} tracking-[0.18em]`} />
            </label>
            <label>
              <span className={LABEL}>{t('accounts.edit.twoFA')}</span>
              <input type="password" placeholder="••••••" className={FIELD} />
            </label>
          </div>
          <button
            type="button"
            className="w-full rounded-[10px] border border-line bg-white py-[9px] text-[13px] font-medium"
          >
            {t('accounts.edit.confirmLogin')}
          </button>
          <div className="mb-[9px] mt-[18px] text-[11px] font-semibold uppercase tracking-[0.04em] text-ink-subtle">
            {t('accounts.edit.import')}
          </div>
          <div className="mb-[10px] flex gap-1 rounded-[10px] bg-canvas p-1">
            {(['session', 'tdata'] as const).map((tab) => (
              <button
                key={tab}
                type="button"
                onClick={() => {
                  setImportTab(tab);
                }}
                className={`flex-1 rounded-[7px] py-[7px] text-[12.5px] font-medium ${importTab === tab ? 'bg-white text-ink shadow-sm' : 'text-ink-muted'}`}
              >
                {tab === 'session' ? '.session' : 'tdata.zip'}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-[11px] rounded-[12px] border border-dashed border-line bg-canvas/40 px-4 py-[14px]">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-[11px] border border-line bg-white text-primary">
              <svg
                width="19"
                height="19"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.7"
              >
                <path d="M16 16l-4-4-4 4M12 12v9" />
                <path d="M20.39 18.39A5 5 0 0 0 18 9h-1.26A8 8 0 1 0 3 16.3" />
              </svg>
            </div>
            <div className="min-w-0">
              <div className="text-[12.5px] font-semibold">{t('accounts.edit.dropTitle')}</div>
              <div className="mt-px text-[11px] text-ink-subtle">{t('accounts.edit.dropHint')}</div>
            </div>
          </div>
        </div>

        <div className="self-start rounded-2xl border border-line bg-white p-5">
          <div className="mb-1 text-[13px] font-semibold">{t('accounts.edit.proxy')}</div>
          <div className="mb-3 text-[12px] text-ink-subtle">{t('accounts.edit.proxyRequired')}</div>
          <div className="mb-3 flex items-center gap-2 rounded-[10px] bg-[#f6f5f2] px-3 py-[10px]">
            <span className="h-2 w-2 rounded-full bg-success" />
            <span className="text-[12.5px] text-[#3a3a3a]">
              {t('accounts.edit.proxyOk')} · {account.proxy_country_code?.toUpperCase() ?? '—'}
            </span>
          </div>
          <div className="mb-3 flex gap-1 rounded-[10px] bg-canvas p-1">
            {(['pool', 'manual'] as const).map((mode) => (
              <button
                key={mode}
                type="button"
                onClick={() => {
                  setProxyMode(mode);
                }}
                className={`flex-1 rounded-[7px] py-[7px] text-[12.5px] font-medium ${proxyMode === mode ? 'bg-white text-ink shadow-sm' : 'text-ink-muted'}`}
              >
                {mode === 'pool' ? t('accounts.edit.fromPool') : t('accounts.edit.manual')}
              </button>
            ))}
          </div>
          {proxyMode === 'manual' ? (
            <>
              <label className="mb-[10px] block">
                <span className={LABEL}>{t('accounts.edit.host')}</span>
                <input className={`${FIELD} font-mono`} />
              </label>
              <div className="mb-[10px] grid grid-cols-2 gap-[10px]">
                <label>
                  <span className={LABEL}>{t('accounts.edit.port')}</span>
                  <input className={`${FIELD} font-mono`} />
                </label>
                <label>
                  <span className={LABEL}>{t('accounts.edit.type')}</span>
                  <select className={FIELD}>
                    <option>SOCKS5</option>
                    <option>HTTPS</option>
                  </select>
                </label>
              </div>
              <div className="grid grid-cols-2 gap-[10px]">
                <label>
                  <span className={LABEL}>{t('accounts.edit.login')}</span>
                  <input className={FIELD} />
                </label>
                <label>
                  <span className={LABEL}>{t('accounts.edit.password')}</span>
                  <div className="relative">
                    <input type={showPass ? 'text' : 'password'} className={`${FIELD} pr-9`} />
                    <button
                      type="button"
                      onClick={() => {
                        setShowPass((value) => !value);
                      }}
                      aria-label={t('accounts.edit.password')}
                      className="absolute right-2 top-1/2 -translate-y-1/2 text-ink-subtle"
                    >
                      <svg
                        width="16"
                        height="16"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="1.8"
                      >
                        <path d="M2 12s3-8 10-8 10 8 10 8-3 8-10 8-10-8-10-8z" />
                        <circle cx="12" cy="12" r="3" />
                      </svg>
                    </button>
                  </div>
                </label>
              </div>
            </>
          ) : (
            <label className="block">
              <span className={LABEL}>{t('accounts.proxyPool.title')}</span>
              <select className={FIELD}>
                <option>nl-1.proxyhub.net:1080</option>
                <option>de-2.proxyhub.net:1080</option>
              </select>
            </label>
          )}
        </div>
      </div>
    </div>
  );
}
