import { useForm, useStore } from '@tanstack/react-form';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  assignProxyMutation,
  checkProxyMutation,
  createProxyMutation,
  proxyPoolQueryOptions,
  unassignProxyMutation,
} from '@/entities/proxy';
import type { AccountRead } from '@/shared/api';
import { FormField } from '@/shared/ui';

import { EMPTY_PROXY_FORM, proxyFormSchema, type ProxyFormValue } from './proxyFormValue';
import { Section, Spinner } from './_shared';
import { FIELD, LABEL, SEG_WRAP, seg, type CheckState } from './_styles';

// Proxy-connection dot per proxy_status (matches the accounts-table palette).
// No proxy_id → unassigned (grey); tcp_working → green; anything else → red.
function proxyDotColor(account: AccountRead): string {
  if (!account.proxy_id) return '#c8c6c2';
  if (account.proxy_status === 'tcp_working') return '#2e9e64';
  return '#c0473f';
}

// Proxy card: state row + detach, pool/manual assignment, and a real connectivity
// check against the assigned proxy.
export function ProxySection({ account }: { account: AccountRead }) {
  const { t } = useTranslation();
  const [proxyMode, setProxyMode] = useState<'pool' | 'manual'>('manual');
  const proxyForm = useForm({
    defaultValues: EMPTY_PROXY_FORM,
    validators: { onChange: proxyFormSchema, onMount: proxyFormSchema },
    onSubmit: ({ value }) => {
      addManualProxy(value);
    },
  });
  const proxyFormCanSubmit = useStore(proxyForm.store, (state) => state.canSubmit);
  const [showPass, setShowPass] = useState(false);
  const [proxyCheck, setProxyCheck] = useState<CheckState>('idle');
  // Real fields returned by the last successful proxy check (country + exit IP).
  const [proxyResult, setProxyResult] = useState<{
    country_code: string | null;
    exit_ip: string | null;
  } | null>(null);

  const queryClient = useQueryClient();
  const proxyMutation = useMutation(checkProxyMutation());
  const createProxy = useMutation(createProxyMutation());
  const assignProxy = useMutation(assignProxyMutation());
  const unassignProxy = useMutation(unassignProxyMutation());
  const pool = useQuery(proxyPoolQueryOptions());
  const freeProxies = (pool.data?.proxies ?? []).filter((proxy) => proxy.free > 0);
  const invalidate = () => {
    void queryClient.invalidateQueries();
  };

  // Record the real fields a proxy check returns (country + exit IP), so the UI
  // renders live data instead of a fabricated flag/latency.
  const applyChecked = (checked: { country_code?: string | null; exit_ip?: string | null }) => {
    setProxyResult({
      country_code: checked.country_code ?? null,
      exit_ip: checked.exit_ip ?? null,
    });
  };

  // Real proxy connectivity check against the assigned pool proxy.
  const runProxyCheck = () => {
    if (!account.proxy_id) {
      setProxyCheck('err');
      return;
    }
    setProxyCheck('loading');
    proxyMutation.mutate(
      { path: { proxy_id: account.proxy_id } },
      {
        onSuccess: (proxy) => {
          applyChecked(proxy);
          setProxyCheck(proxy.status === 'tcp_working' ? 'ok' : 'err');
          invalidate();
        },
        onError: () => {
          setProxyCheck('err');
        },
      },
    );
  };

  // Pool mode: picking a free pool proxy reassigns this account immediately.
  const assignFromPool = (proxyId: string) => {
    if (!proxyId) return;
    assignProxy.mutate(
      { path: { proxy_id: proxyId }, body: { account_id: account.account_id } },
      { onSuccess: invalidate },
    );
  };

  // Detach the assigned proxy, leaving the account proxyless (the only path to
  // that state — pool assign only ever replaces).
  const onUnassign = () => {
    if (!account.proxy_id) return;
    unassignProxy.mutate(
      { body: { account_id: account.account_id } },
      {
        onSuccess: () => {
          setProxyResult(null);
          setProxyCheck('idle');
          invalidate();
        },
      },
    );
  };

  // Manual mode: create the entered proxy (idempotent), assign it, verify it.
  const addManualProxy = (form: ProxyFormValue) => {
    setProxyCheck('loading');
    createProxy.mutate(
      {
        body: {
          proxy_type: form.proxy_type,
          host: form.host.trim(),
          port: Number(form.port),
          username: form.username.trim() || null,
          password: form.password || null,
        },
      },
      {
        onSuccess: (created) => {
          assignProxy.mutate(
            { path: { proxy_id: created.id }, body: { account_id: account.account_id } },
            {
              onSuccess: () => {
                proxyMutation.mutate(
                  { path: { proxy_id: created.id } },
                  {
                    onSuccess: (checked) => {
                      applyChecked(checked);
                      setProxyCheck(checked.status === 'tcp_working' ? 'ok' : 'err');
                      invalidate();
                    },
                    onError: () => {
                      setProxyCheck('err');
                    },
                  },
                );
              },
              onError: () => {
                setProxyCheck('err');
              },
            },
          );
        },
        onError: () => {
          setProxyCheck('err');
        },
      },
    );
  };

  const onProxyAction = () => {
    if (proxyMode === 'manual') void proxyForm.handleSubmit();
    else runProxyCheck();
  };

  const country = account.proxy_country_code?.toUpperCase() ?? '—';
  const proxyDot = proxyDotColor(account);
  const proxyStateText = !account.proxy_id
    ? t('accounts.edit.proxyNone')
    : account.proxy_status === 'tcp_working'
      ? `${t('accounts.edit.proxyOk')} · ${country}`
      : `${t('accounts.edit.proxyFailed')} · ${country}`;

  return (
    <Section title={t('accounts.edit.proxy')}>
      <div className="mb-3 text-[12px] text-ink-subtle">{t('accounts.edit.proxyRequired')}</div>
      <div className="mb-3 flex items-center justify-between gap-2 rounded-[10px] bg-[#f6f5f2] px-3 py-[10px]">
        <span className="flex items-center gap-2">
          <span className="h-2 w-2 rounded-full" style={{ background: proxyDot }} />
          <span className="text-[12.5px] text-[#3a3a3a]">{proxyStateText}</span>
        </span>
        {account.proxy_id ? (
          <button
            type="button"
            onClick={onUnassign}
            disabled={unassignProxy.isPending}
            className="rounded-[8px] border border-line-input bg-white px-3 py-[5px] text-[12px] font-medium text-ink-muted disabled:opacity-50"
          >
            {unassignProxy.isPending ? <Spinner size={12} /> : t('accounts.edit.proxyDetach')}
          </button>
        ) : null}
      </div>
      {unassignProxy.isError ? (
        <div className="mb-3 text-[11.5px] text-[#c0473f]">{t('accounts.edit.proxyDetachErr')}</div>
      ) : null}
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
          <div className="mb-[10px]">
            <proxyForm.Field name="host">
              {(field) => (
                <FormField field={field} label={t('accounts.edit.host')} className="font-mono" />
              )}
            </proxyForm.Field>
          </div>
          <div className="mb-[10px] grid grid-cols-2 gap-[10px]">
            <proxyForm.Field name="port">
              {(field) => (
                <FormField
                  field={field}
                  label={t('accounts.edit.port')}
                  inputMode="numeric"
                  className="font-mono"
                />
              )}
            </proxyForm.Field>
            <proxyForm.Field name="proxy_type">
              {(field) => (
                <label className="block">
                  <span className={LABEL}>{t('accounts.edit.type')}</span>
                  <select
                    value={field.state.value}
                    onChange={(event) => {
                      field.handleChange(event.target.value as ProxyFormValue['proxy_type']);
                    }}
                    className={FIELD}
                  >
                    <option value="socks5">SOCKS5</option>
                    <option value="https">HTTPS</option>
                  </select>
                </label>
              )}
            </proxyForm.Field>
          </div>
          <div className="mb-[14px] grid grid-cols-2 gap-[10px]">
            <proxyForm.Field name="username">
              {(field) => <FormField field={field} label={t('accounts.edit.login')} />}
            </proxyForm.Field>
            <proxyForm.Field name="password">
              {(field) => (
                <label className="block">
                  <span className={LABEL}>{t('accounts.edit.password')}</span>
                  <div className="relative">
                    <input
                      value={field.state.value}
                      onChange={(event) => {
                        field.handleChange(event.target.value);
                      }}
                      onBlur={field.handleBlur}
                      type={showPass ? 'text' : 'password'}
                      className={`${FIELD} pr-9`}
                    />
                    <button
                      type="button"
                      onClick={() => {
                        setShowPass((value) => !value);
                      }}
                      aria-label={t('accounts.edit.password')}
                      className="absolute right-2 top-1/2 -translate-y-1/2 text-ink-subtle"
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
            </proxyForm.Field>
          </div>
        </>
      ) : (
        <label className="mb-[14px] block">
          <span className={LABEL}>{t('accounts.proxyPool.title')}</span>
          <select
            value={account.proxy_id ?? ''}
            onChange={(event) => {
              assignFromPool(event.target.value);
            }}
            className={FIELD}
          >
            <option value="">{t('accounts.edit.choosePoolProxy')}</option>
            {freeProxies.map((proxy) => (
              <option key={proxy.id} value={proxy.id}>
                {proxy.host}:{proxy.port}
              </option>
            ))}
          </select>
        </label>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={onProxyAction}
          disabled={proxyMode === 'manual' && !proxyFormCanSubmit}
          className="inline-flex items-center gap-[7px] rounded-full border border-line-input bg-white px-4 py-2 text-[13px] font-medium disabled:opacity-50"
        >
          {proxyCheck === 'loading' ? (
            <Spinner size={13} />
          ) : (
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.9"
            >
              <path d="M21 12a9 9 0 1 1-6.22-8.56" />
              <path d="M21 3v6h-6" />
            </svg>
          )}
          {t('accounts.edit.proxyCheck')}
        </button>
        {proxyCheck === 'loading' && (
          <span className="text-[12.5px] text-ink-subtle">{t('accounts.edit.proxyChecking')}</span>
        )}
        {proxyCheck === 'ok' && (
          <span className="tb-pop inline-flex items-center gap-[6px] rounded-full bg-[#e7f2ec] px-3 py-[5px] text-[12.5px] font-medium text-[#2e7d55]">
            {proxyResult?.country_code ? (
              <span
                className={`fi fi-${proxyResult.country_code.toLowerCase()} inline-block h-[13px] w-[18px] rounded-[2px] shadow-[0_0_0_1px_rgba(0,0,0,.07)]`}
              />
            ) : null}
            {[proxyResult?.country_code?.toUpperCase(), proxyResult?.exit_ip]
              .filter(Boolean)
              .join(' · ') || t('accounts.edit.proxyReachable')}
          </span>
        )}
        {proxyCheck === 'err' && (
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
            {t('accounts.edit.proxyDown')}
          </span>
        )}
      </div>
    </Section>
  );
}
