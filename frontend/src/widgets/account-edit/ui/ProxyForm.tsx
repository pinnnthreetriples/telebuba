import { useForm, useStore } from '@tanstack/react-form';
import { useMutation } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { probeProxyMutation } from '@/entities/proxy';
import { Badge, Button, FormField, Icon, Input, SegmentedControl } from '@/shared/ui';

import { proxyFormSchema, type ProxyFormValue } from './proxyFormValue';

// Shared proxy-form fields (host / port / type / login / password+eye + a real
// connectivity probe), now on @tanstack/react-form + zod. The form owns field
// state/validation; it publishes the current value + validity up to the parent
// (the add-proxy modal owns the value + the create call), so the parent's footer
// button stays the submit trigger. The probe hits POST /proxies/probe (stateless)
// so the operator can verify before adding.
const LABEL = 'mb-tight block type-label';

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
    <div className="flex flex-col gap-md">
      <div className="grid grid-cols-1 md:grid-cols-[2fr_1fr] gap-md">
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
      <div className="grid grid-cols-1 md:grid-cols-2 gap-md">
        <form.Field name="username">
          {(field) => (
            <FormField
              field={field}
              label={t('accounts.proxyForm.login')}
              placeholder={t('accounts.proxyForm.loginPlaceholder')}
              // name="username" beside a password input is the formless login
              // shape browsers autofill; both halves opt out (see ProxySection).
              autoComplete="off"
            />
          )}
        </form.Field>
        <form.Field name="password">
          {(field) => (
            <label className="block">
              <span className={LABEL}>{t('accounts.proxyForm.password')}</span>
              <div className="relative">
                <Input
                  value={field.state.value}
                  onChange={(event) => {
                    field.handleChange(event.target.value);
                  }}
                  onBlur={field.handleBlur}
                  type={showPass ? 'text' : 'password'}
                  autoComplete="new-password"
                  placeholder={t('accounts.proxyForm.passwordPlaceholder')}
                  className="pr-[36px]"
                />
                <button
                  type="button"
                  onClick={() => {
                    setShowPass((shown) => !shown);
                  }}
                  aria-label={t('accounts.proxyForm.password')}
                  className="absolute right-[6px] top-1/2 flex size-icon -translate-y-1/2 items-center justify-center text-content-subtle"
                >
                  {showPass ? <Icon name="eye-off" size={16} /> : <Icon name="eye" size={16} />}
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
            <SegmentedControl
              value={field.state.value}
              ariaLabel={t('accounts.proxyForm.type')}
              options={(['socks5', 'https'] as const).map((option) => ({
                value: option,
                label: option.toUpperCase(),
              }))}
              onChange={(option) => {
                field.handleChange(option);
              }}
            />
          )}
        </form.Field>
      </div>
      <div className="flex flex-wrap items-center gap-md">
        <Button
          size="sm"
          className="items-center gap-sm"
          onClick={runDetect}
          disabled={!canProbe}
          loading={detect === 'loading'}
        >
          {detect !== 'loading' && (
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
        </Button>
        {detect === 'loading' && (
          <span className="type-prose">{t('accounts.proxyForm.checking')}</span>
        )}
        {detect === 'ok' && (
          <Badge tone="success" size="md" className="tb-pop gap-sm">
            {country ? (
              <span
                className={`fi fi-${country.toLowerCase()} inline-block h-flag w-flag rounded-[2px] shadow-ring`}
              />
            ) : null}
            {country ?? t('accounts.proxyForm.resultOk')}
          </Badge>
        )}
        {detect === 'err' && (
          <span className="inline-flex items-center gap-sm type-label text-danger">
            <Icon name="x-circle" size={14} />
            {t('accounts.proxyForm.resultErr')}
          </span>
        )}
      </div>
    </div>
  );
}
