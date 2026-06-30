import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { probeProxyMutation } from '@/entities/proxy';

import type { ProxyFormValue } from './proxyFormValue';

// Shared proxy-form fields (host / port / type / login / password+eye + a real
// connectivity probe). Controlled by the parent (the add-proxy modal owns the
// value + the create call); the probe hits POST /proxies/probe (stateless, no
// persistence) so the operator can verify before adding.
const FIELD =
  'tb-time w-full rounded-[10px] border border-line-input bg-white px-3 py-[9px] text-[13px] outline-none';
const LABEL = 'mb-[6px] block text-[12px] font-medium text-[#3a3a3a]';

type DetectState = 'idle' | 'loading' | 'ok' | 'err';

export function ProxyForm({
  value,
  onChange,
}: {
  value: ProxyFormValue;
  onChange: (value: ProxyFormValue) => void;
}) {
  const { t } = useTranslation();
  const [showPass, setShowPass] = useState(false);
  const [detect, setDetect] = useState<DetectState>('idle');
  const [country, setCountry] = useState<string | null>(null);
  const probe = useMutation(probeProxyMutation());

  const seg = (on: boolean): string =>
    `flex-1 rounded-[7px] py-[7px] text-[12.5px] font-medium transition ${on ? 'bg-white text-ink shadow-sm' : 'text-ink-muted'}`;
  const set = (patch: Partial<ProxyFormValue>) => {
    onChange({ ...value, ...patch });
  };

  const runDetect = () => {
    setDetect('loading');
    probe.mutate(
      {
        body: {
          proxy_type: value.proxy_type,
          host: value.host.trim(),
          port: Number(value.port),
          username: value.username.trim() || null,
          password: value.password || null,
        },
      },
      {
        onSuccess: (result) => {
          setCountry(result.country_code ?? null);
          setDetect(result.status === 'tcp_working' ? 'ok' : 'err');
        },
        onError: () => {
          setDetect('err');
        },
      },
    );
  };

  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-[2fr_1fr] gap-[10px]">
        <label>
          <span className={LABEL}>{t('accounts.proxyForm.host')}</span>
          <input
            value={value.host}
            onChange={(event) => {
              set({ host: event.target.value });
            }}
            placeholder="123.45.67.89"
            className={`${FIELD} font-mono`}
          />
        </label>
        <label>
          <span className={LABEL}>{t('accounts.proxyForm.port')}</span>
          <input
            value={value.port}
            inputMode="numeric"
            onChange={(event) => {
              set({ port: event.target.value.replace(/\D/g, '') });
            }}
            placeholder="1080"
            className={`${FIELD} font-mono`}
          />
        </label>
      </div>
      <div className="grid grid-cols-2 gap-[10px]">
        <label>
          <span className={LABEL}>{t('accounts.proxyForm.login')}</span>
          <input
            value={value.username}
            onChange={(event) => {
              set({ username: event.target.value });
            }}
            placeholder={t('accounts.proxyForm.loginPlaceholder')}
            className={FIELD}
          />
        </label>
        <label>
          <span className={LABEL}>{t('accounts.proxyForm.password')}</span>
          <div className="relative">
            <input
              value={value.password}
              onChange={(event) => {
                set({ password: event.target.value });
              }}
              type={showPass ? 'text' : 'password'}
              placeholder={t('accounts.proxyForm.passwordPlaceholder')}
              className={`${FIELD} pr-9`}
            />
            <button
              type="button"
              onClick={() => {
                setShowPass((shown) => !shown);
              }}
              aria-label={t('accounts.proxyForm.password')}
              className="absolute right-[6px] top-1/2 flex h-[26px] w-[26px] -translate-y-1/2 items-center justify-center text-ink-subtle"
            >
              {showPass ? (
                <svg
                  width="16"
                  height="16"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.8"
                >
                  <path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 10 8 10 8a13.16 13.16 0 0 1-1.67 2.68" />
                  <path d="M6.61 6.61A13.5 13.5 0 0 0 2 12s3 8 10 8a9.12 9.12 0 0 0 5.39-1.61" />
                  <path d="M14.12 14.12A3 3 0 1 1 9.88 9.88" />
                  <path d="M1 1l22 22" />
                </svg>
              ) : (
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
              )}
            </button>
          </div>
        </label>
      </div>
      <div>
        <span className={LABEL}>{t('accounts.proxyForm.type')}</span>
        <div className="flex gap-1 rounded-[10px] bg-[#f1efed] p-1">
          {(['socks5', 'https'] as const).map((option) => (
            <button
              key={option}
              type="button"
              onClick={() => {
                set({ proxy_type: option });
              }}
              className={seg(value.proxy_type === option)}
            >
              {option.toUpperCase()}
            </button>
          ))}
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={runDetect}
          disabled={detect === 'loading' || !value.host || !value.port}
          className="inline-flex items-center gap-[7px] rounded-full border border-line-input bg-white px-4 py-2 text-[13px] font-medium disabled:opacity-50"
        >
          {detect === 'loading' ? (
            <span className="tb-spin inline-block h-[13px] w-[13px] rounded-full border-2 border-[#c8c6c2] border-t-primary" />
          ) : (
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.9"
            >
              <path d="M21 10c0 7-9 12-9 12s-9-5-9-12a9 9 0 0 1 18 0z" />
              <circle cx="12" cy="10" r="3" />
            </svg>
          )}
          {detect === 'ok' ? t('accounts.proxyForm.detected') : t('accounts.proxyForm.detect')}
        </button>
        {detect === 'loading' && (
          <span className="text-[12.5px] text-ink-subtle">{t('accounts.proxyForm.checking')}</span>
        )}
        {detect === 'ok' && (
          <span className="tb-pop inline-flex items-center gap-[6px] rounded-full bg-[#e7f2ec] px-3 py-[5px] text-[12.5px] font-medium text-[#2e7d55]">
            {country ? (
              <span
                className={`fi fi-${country.toLowerCase()} inline-block h-[13px] w-[18px] rounded-[2px] shadow-[0_0_0_1px_rgba(0,0,0,.07)]`}
              />
            ) : null}
            {country ?? t('accounts.proxyForm.resultOk')}
          </span>
        )}
        {detect === 'err' && (
          <span className="inline-flex items-center gap-[6px] text-[12.5px] font-medium text-[#c0473f]">
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            >
              <circle cx="12" cy="12" r="10" />
              <path d="m15 9-6 6M9 9l6 6" />
            </svg>
            {t('accounts.proxyForm.resultErr')}
          </span>
        )}
      </div>
    </div>
  );
}
