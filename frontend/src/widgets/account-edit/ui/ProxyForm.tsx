import { useState } from 'react';
import { useTranslation } from 'react-i18next';

// Shared proxy-form fields (host / port / type / login / password+eye + a
// detect button with idle→loading→(ok|err) states), reused by the add-account
// wizard's proxy step and the standalone proxy-add modal. ponytail: design-first
// — detect is a visual mock, no backend call.
const FIELD =
  'tb-time w-full rounded-[10px] border border-line-input bg-white px-3 py-[9px] text-[13px] outline-none';
const LABEL = 'mb-[6px] block text-[12px] font-medium text-[#3a3a3a]';

type DetectState = 'idle' | 'loading' | 'ok' | 'err';

export function ProxyForm() {
  const { t } = useTranslation();
  const [type, setType] = useState<'SOCKS5' | 'HTTPS'>('SOCKS5');
  const [showPass, setShowPass] = useState(false);
  const [detect, setDetect] = useState<DetectState>('idle');

  const seg = (on: boolean): string =>
    `flex-1 rounded-[7px] py-[7px] text-[12.5px] font-medium transition ${on ? 'bg-white text-ink shadow-sm' : 'text-ink-muted'}`;

  const runDetect = () => {
    setDetect('loading');
    setTimeout(() => {
      setDetect('ok');
    }, 900);
  };

  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-[2fr_1fr] gap-[10px]">
        <label>
          <span className={LABEL}>{t('accounts.proxyForm.host')}</span>
          <input placeholder="123.45.67.89" className={`${FIELD} font-mono`} />
        </label>
        <label>
          <span className={LABEL}>{t('accounts.proxyForm.port')}</span>
          <input placeholder="1080" className={`${FIELD} font-mono`} />
        </label>
      </div>
      <div className="grid grid-cols-2 gap-[10px]">
        <label>
          <span className={LABEL}>{t('accounts.proxyForm.login')}</span>
          <input placeholder={t('accounts.proxyForm.loginPlaceholder')} className={FIELD} />
        </label>
        <label>
          <span className={LABEL}>{t('accounts.proxyForm.password')}</span>
          <div className="relative">
            <input
              type={showPass ? 'text' : 'password'}
              placeholder={t('accounts.proxyForm.passwordPlaceholder')}
              className={`${FIELD} pr-9`}
            />
            <button
              type="button"
              onClick={() => {
                setShowPass((value) => !value);
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
          {(['SOCKS5', 'HTTPS'] as const).map((value) => (
            <button
              key={value}
              type="button"
              onClick={() => {
                setType(value);
              }}
              className={seg(type === value)}
            >
              {value}
            </button>
          ))}
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={runDetect}
          className="inline-flex items-center gap-[7px] rounded-full border border-line-input bg-white px-4 py-2 text-[13px] font-medium"
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
            <span className="inline-block h-[13px] w-[18px] rounded-[2px] bg-[#21468b] shadow-[0_0_0_1px_rgba(0,0,0,.07)]" />
            {t('accounts.proxyForm.resultOk')}
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
