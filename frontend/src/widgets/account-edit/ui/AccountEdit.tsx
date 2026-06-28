import { useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { StatusBadge } from '@/entities/account';
import type { AccountRead } from '@/shared/api';

const FIELD =
  'tb-time w-full rounded-[10px] border border-line bg-white px-3 py-[9px] text-[13px] outline-none';
const FIELD_LOCKED =
  'w-full cursor-not-allowed rounded-[10px] border border-line bg-[#f6f5f2] px-3 py-[9px] text-[13px] text-ink-subtle outline-none';
const LABEL = 'mb-[6px] block text-[12px] font-medium text-[#3a3a3a]';
const SEG_WRAP = 'mb-[10px] flex gap-1 rounded-[10px] bg-[#f1efed] p-1';
const seg = (on: boolean): string =>
  `flex-1 rounded-[7px] py-[7px] text-[12.5px] font-medium transition ${on ? 'bg-white text-ink shadow-sm' : 'text-ink-muted'}`;

// ponytail: device profile + spam signals are mock until AccountRead carries
// them — design-first, backend wiring is a later step.
const DEVICE = { model: 'iPhone 13', os: 'iOS 17.2', lang: 'Русский (ru-RU)' };
const SIGNALS = [
  { dot: 'bg-[#2e9e64]', label: 'Текущий статус', value: 'Без ограничений' },
  { dot: 'bg-line-strong', label: 'Последний spam-block', value: 'не зафиксирован' },
  { dot: 'bg-line-strong', label: 'Последняя проверка', value: 'сегодня' },
];

function mono(account: AccountRead): string {
  return (account.phone ?? account.account_id).replace(/\D/g, '').slice(-2) || '#';
}

function Chevron({ open }: { open: boolean }) {
  return (
    <span
      className={`flex text-ink-subtle transition-transform duration-[420ms] [transition-timing-function:cubic-bezier(.34,1.45,.6,1)] ${open ? 'rotate-180' : ''}`}
    >
      <svg
        width="16"
        height="16"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
      >
        <path d="m6 9 6 6 6-6" />
      </svg>
    </span>
  );
}

// A design accordion card: header (title + chevron) + max-height-collapsing body.
// `right` renders an action between the title and chevron (the signals @SpamBot check).
function Section({
  title,
  icon,
  right,
  bodyClassName = 'px-5 pb-[18px]',
  children,
}: {
  title: string;
  icon?: ReactNode;
  right?: ReactNode;
  bodyClassName?: string;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const toggle = () => {
    setOpen((value) => !value);
  };
  const heading = (
    <span className="flex items-center gap-[7px] text-[13px] font-semibold text-ink">
      {title}
      {icon}
    </span>
  );
  return (
    <div className="self-start overflow-hidden rounded-2xl border border-line bg-white">
      {right ? (
        <div className="flex items-center gap-[10px] px-5 py-4">
          <button
            type="button"
            onClick={toggle}
            className="flex flex-1 items-center gap-[10px] text-left"
          >
            {heading}
          </button>
          {right}
          <button
            type="button"
            onClick={toggle}
            aria-label={title}
            className="flex shrink-0 items-center"
          >
            <Chevron open={open} />
          </button>
        </div>
      ) : (
        <button
          type="button"
          onClick={toggle}
          className="flex w-full items-center justify-between gap-[10px] px-5 py-4 text-left"
        >
          {heading}
          <Chevron open={open} />
        </button>
      )}
      <div className={`tb-collapse ${open ? 'tb-open' : ''}`}>
        <div className={bodyClassName}>{children}</div>
      </div>
    </div>
  );
}

// The design's account-edit view (reached by clicking a row): an always-visible
// hero header above five collapsible cards — session, proxy, device, signals,
// actions. ponytail: every form here is mock until the backend is wired.
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

      <div className="mb-[14px] grid grid-cols-2 gap-[14px]">
        <Section title={t('accounts.edit.session')}>
          <div className="mb-[10px] flex items-center justify-between gap-[10px] rounded-[10px] bg-[#f6f5f2] px-3 py-[10px]">
            <span className="flex items-center gap-2">
              <span className="h-2 w-2 rounded-full bg-success-dot" />
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
          <div className={SEG_WRAP}>
            {(['session', 'tdata'] as const).map((tab) => (
              <button
                key={tab}
                type="button"
                onClick={() => {
                  setImportTab(tab);
                }}
                className={seg(importTab === tab)}
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
        </Section>

        <Section title={t('accounts.edit.proxy')}>
          <div className="mb-3 text-[12px] text-ink-subtle">{t('accounts.edit.proxyRequired')}</div>
          <div className="mb-3 flex items-center gap-2 rounded-[10px] bg-[#f6f5f2] px-3 py-[10px]">
            <span className="h-2 w-2 rounded-full bg-success-dot" />
            <span className="text-[12.5px] text-[#3a3a3a]">
              {t('accounts.edit.proxyOk')} · {account.proxy_country_code?.toUpperCase() ?? '—'}
            </span>
          </div>
          <div className={SEG_WRAP}>
            {(['pool', 'manual'] as const).map((mode) => (
              <button
                key={mode}
                type="button"
                onClick={() => {
                  setProxyMode(mode);
                }}
                className={seg(proxyMode === mode)}
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
              <div className="mb-[14px] grid grid-cols-2 gap-[10px]">
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
            <label className="mb-[14px] block">
              <span className={LABEL}>{t('accounts.proxyPool.title')}</span>
              <select className={FIELD}>
                <option>nl-1.proxyhub.net:1080</option>
                <option>de-2.proxyhub.net:1080</option>
              </select>
            </label>
          )}
          <button
            type="button"
            className="inline-flex items-center gap-[7px] rounded-full border border-line bg-white px-4 py-2 text-[13px] font-medium"
          >
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.9"
            >
              <path d="M21 12a9 9 0 1 1-6.2-8.6" />
              <path d="M21 3v6h-6" />
            </svg>
            {t('accounts.edit.proxyCheck')}
          </button>
        </Section>
      </div>

      <div className="mb-[14px] grid grid-cols-2 gap-[14px]">
        <Section
          title={t('accounts.edit.device')}
          icon={
            <svg
              width="13"
              height="13"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              className="text-ink-subtle"
            >
              <rect x="3" y="11" width="18" height="11" rx="2" />
              <path d="M7 11V7a5 5 0 0 1 10 0v4" />
            </svg>
          }
        >
          <div className="mb-[14px] text-[12px] text-ink-subtle">
            {t('accounts.edit.deviceLocked')}
          </div>
          <div className="flex flex-col gap-[11px]">
            <label>
              <span className={LABEL}>{t('accounts.edit.deviceModel')}</span>
              <input value={DEVICE.model} disabled className={FIELD_LOCKED} />
            </label>
            <label>
              <span className={LABEL}>{t('accounts.edit.deviceOs')}</span>
              <input value={DEVICE.os} disabled className={FIELD_LOCKED} />
            </label>
            <label>
              <span className={LABEL}>{t('accounts.edit.deviceLang')}</span>
              <input value={DEVICE.lang} disabled className={FIELD_LOCKED} />
            </label>
          </div>
        </Section>

        <Section
          title={t('accounts.edit.signals')}
          right={
            <span className="tb-tip">
              <button
                type="button"
                className="inline-flex items-center gap-[6px] rounded-full border border-line bg-white px-3 py-[5px] text-[12px] font-medium text-ink-muted"
              >
                {t('accounts.edit.signalsCheck')}
              </button>
              <span className="tb-tip-pop">{t('accounts.edit.signalsTip')}</span>
            </span>
          }
        >
          <div className="mb-2 text-[12px] text-ink-subtle">
            {t('accounts.edit.signalsReadonly')}
          </div>
          <div className="flex flex-col">
            {SIGNALS.map((signal) => (
              <div
                key={signal.label}
                className="flex items-center justify-between gap-3 border-b border-[#f0eeeb] py-[11px]"
              >
                <span className="flex items-center gap-2 text-[12.5px] text-ink-muted">
                  <span className={`h-[7px] w-[7px] shrink-0 rounded-full ${signal.dot}`} />
                  {signal.label}
                </span>
                <span className="text-right text-[12.5px] font-medium text-ink">
                  {signal.value}
                </span>
              </div>
            ))}
          </div>
        </Section>
      </div>

      <Section title={t('accounts.edit.actions')} bodyClassName="px-5 pb-[6px]">
        <div className="flex items-center justify-between gap-3 border-b border-[#f0eeeb] py-[14px]">
          <div>
            <div className="text-[13px] font-medium">{t('accounts.edit.aliveTitle')}</div>
            <div className="mt-px text-[11.5px] text-ink-subtle">
              {t('accounts.edit.aliveHint')}
            </div>
          </div>
          <button
            type="button"
            title={t('accounts.edit.aliveBtnTitle')}
            aria-label={t('accounts.edit.aliveBtnTitle')}
            className="flex h-[30px] w-[30px] shrink-0 items-center justify-center rounded-full border border-line bg-white text-ink-muted"
          >
            <svg
              width="17"
              height="17"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M21 12a9 9 0 1 1-6.2-8.6" />
              <path d="M21 3v6h-6" />
            </svg>
          </button>
        </div>
        <div className="flex items-center justify-between gap-3 border-b border-[#f0eeeb] py-[14px]">
          <div>
            <div className="text-[13px] font-medium">{t('accounts.edit.resetSession')}</div>
            <div className="mt-px text-[11.5px] text-ink-subtle">
              {t('accounts.edit.resetSessionHint')}
            </div>
          </div>
          <button
            type="button"
            className="shrink-0 rounded-full border border-line bg-white px-4 py-2 text-[13px] font-medium"
          >
            {t('accounts.edit.reset')}
          </button>
        </div>
        <div className="flex items-center justify-between gap-3 py-[14px]">
          <div>
            <div className="text-[13px] font-medium">{t('accounts.edit.deleteAccount')}</div>
            <div className="mt-px text-[11.5px] text-ink-subtle">
              {t('accounts.edit.deleteHint')}
            </div>
          </div>
          <button
            type="button"
            className="shrink-0 px-1 py-2 text-[13px] font-medium text-[#c0473f]"
          >
            {t('accounts.edit.deleteAccount')}
          </button>
        </div>
      </Section>
    </div>
  );
}
