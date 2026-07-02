import { useForm, useStore } from '@tanstack/react-form';
import { useMutation } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { probeProxyMutation } from '@/entities/proxy';
import { FormField } from '@/shared/ui';

import { proxyFormSchema, type ProxyFormValue } from './proxyFormValue';

// Shared proxy-form fields (host / port / type / login / password+eye + a real
// connectivity probe), now on @tanstack/react-form + zod. The form owns field
// state/validation; it publishes the current value + validity up to the parent
// (the add-proxy modal owns the value + the create call), so the parent's footer
// button stays the submit trigger. The probe hits POST /proxies/probe (stateless)
// so the operator can verify before adding.
const LABEL = 'mb-[6px] block text-[12px] font-medium text-[#3a3a3a]';
const PASS_FIELD =
  'tb-time w-full rounded-[10px] border border-line-input bg-white px-3 py-[9px] pr-9 text-[13px] outline-none';

type DetectState = 'idle' | 'loading' | 'ok' | 'err';

export function ProxyForm({
  value,
  onChange,
  onValidityChange,
}: {
  value: ProxyFormValue;
  onChange: (value: ProxyFormValue) => void;
  onValidityChange?: (valid: boolean) => void;
}) {
  const { t } = useTranslation();
  const [showPass, setShowPass] = useState(false);
  const [detect, setDetect] = useState<DetectState>('idle');
  const [country, setCountry] = useState<string | null>(null);
  const probe = useMutation(probeProxyMutation());

  const form = useForm({
    defaultValues: value,
    validators: { onChange: proxyFormSchema, onMount: proxyFormSchema },
  });

  // Publish the form's live value + validity to the parent (which owns submission).
  const values = useStore(form.store, (state) => state.values);
  const canSubmit = useStore(form.store, (state) => state.canSubmit);
  useEffect(() => {
    onChange(values);
  }, [values, onChange]);
  useEffect(() => {
    onValidityChange?.(canSubmit);
  }, [canSubmit, onValidityChange]);

  const seg = (on: boolean): string =>
    `flex-1 rounded-[7px] py-[7px] text-[12.5px] font-medium transition ${on ? 'bg-white text-ink shadow-sm' : 'text-ink-muted'}`;
  const canProbe = detect !== 'loading' && values.host.trim() !== '' && values.port !== '';

  const runDetect = () => {
    setDetect('loading');
    probe.mutate(
      {
        body: {
          proxy_type: values.proxy_type,
          host: values.host.trim(),
          port: Number(values.port),
          username: values.username.trim() || null,
          password: values.password || null,
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
        <form.Field name="host">
          {(field) => (
            <FormField
              field={field}
              label={t('accounts.proxyForm.host')}
              placeholder="123.45.67.89"
              className="font-mono"
            />
          )}
        </form.Field>
        <form.Field name="port">
          {(field) => (
            <FormField
              field={field}
              label={t('accounts.proxyForm.port')}
              inputMode="numeric"
              placeholder="1080"
              className="font-mono"
            />
          )}
        </form.Field>
      </div>
      <div className="grid grid-cols-2 gap-[10px]">
        <form.Field name="username">
          {(field) => (
            <FormField
              field={field}
              label={t('accounts.proxyForm.login')}
              placeholder={t('accounts.proxyForm.loginPlaceholder')}
            />
          )}
        </form.Field>
        <form.Field name="password">
          {(field) => (
            <label className="block">
              <span className={LABEL}>{t('accounts.proxyForm.password')}</span>
              <div className="relative">
                <input
                  value={field.state.value}
                  onChange={(event) => {
                    field.handleChange(event.target.value);
                  }}
                  onBlur={field.handleBlur}
                  type={showPass ? 'text' : 'password'}
                  placeholder={t('accounts.proxyForm.passwordPlaceholder')}
                  className={PASS_FIELD}
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
          )}
        </form.Field>
      </div>
      <div>
        <span className={LABEL}>{t('accounts.proxyForm.type')}</span>
        <form.Field name="proxy_type">
          {(field) => (
            <div className="flex gap-1 rounded-[10px] bg-[#f1efed] p-1">
              {(['socks5', 'https'] as const).map((option) => (
                <button
                  key={option}
                  type="button"
                  onClick={() => {
                    field.handleChange(option);
                  }}
                  className={seg(field.state.value === option)}
                >
                  {option.toUpperCase()}
                </button>
              ))}
            </div>
          )}
        </form.Field>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={runDetect}
          disabled={!canProbe}
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
