import { useForm, useStore } from '@tanstack/react-form';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { invalidateAccountViews } from '@/entities/account';
import {
  assignProxyMutation,
  checkProxyMutation,
  createProxyMutation,
  proxyPoolQueryOptions,
  unassignProxyMutation,
} from '@/entities/proxy';
import type { AccountRead } from '@/shared/api';
import {
  Badge,
  Button,
  ConfirmModal,
  FormField,
  Input,
  Select,
  type SelectOption,
} from '@/shared/ui';

import { EMPTY_PROXY_FORM, proxyFormSchema, type ProxyFormValue } from './proxyFormValue';
import { Section, Spinner } from './_shared';
import { LABEL, SEG_WRAP, seg, type CheckState } from './_styles';

// Protocol names, not copy — nothing to translate.
const PROXY_TYPES: SelectOption[] = [
  { value: 'socks5', label: 'SOCKS5' },
  { value: 'https', label: 'HTTPS' },
];

// Proxy-connection dot per proxy_status, as the tokens the states MEAN (the same
// three the accounts table paints). No proxy_id → unassigned (grey);
// tcp_working → green; anything else → red.
function proxyDotTone(account: AccountRead): string {
  if (!account.proxy_id) return 'bg-line-strong';
  if (account.proxy_status === 'tcp_working') return 'bg-success';
  return 'bg-danger';
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
  const [confirmReplace, setConfirmReplace] = useState(false);
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
  // Free proxies PLUS the one this account already holds: filtering on `free`
  // alone dropped a proxy at capacity — including when THIS account holds its
  // last slot — so the select fell back to "choose from pool" while the state
  // row above said "connected".
  const poolChoices = (pool.data?.proxies ?? []).filter(
    (proxy) => proxy.free > 0 || proxy.id === account.proxy_id,
  );
  const invalidate = () => {
    invalidateAccountViews(queryClient);
  };
  // Every write path here is create → assign → check; without one gate a
  // double click fires two chains that resolve out of order.
  const proxyBusy = createProxy.isPending || assignProxy.isPending || proxyMutation.isPending;

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

  // Pool mode really is a check of the assigned proxy. Manual mode is not: it
  // creates the entered proxy and MOVES the account onto it (the backend assign
  // is an unconditional update + evict_client, so the live session reconnects
  // through it), and re-adding an endpoint already in the pool rewrites that
  // shared row's credentials. Ask first when a proxy is already assigned.
  const onProxyAction = () => {
    if (proxyMode !== 'manual') {
      runProxyCheck();
      return;
    }
    if (account.proxy_id) {
      setConfirmReplace(true);
      return;
    }
    void proxyForm.handleSubmit();
  };

  const country = account.proxy_country_code?.toUpperCase() ?? '—';
  const proxyDot = proxyDotTone(account);
  const proxyStateText = !account.proxy_id
    ? t('accounts.edit.proxyNone')
    : account.proxy_status === 'tcp_working'
      ? `${t('accounts.edit.proxyOk')} · ${country}`
      : `${t('accounts.edit.proxyFailed')} · ${country}`;

  return (
    <Section title={t('accounts.edit.proxy')}>
      <div className="mb-md text-body text-ink-subtle">{t('accounts.edit.proxyRequired')}</div>
      <div className="mb-md flex items-center justify-between gap-sm rounded-lg bg-canvas px-md py-md">
        <span className="flex items-center gap-sm">
          <span className={`h-2 w-2 rounded-full ${proxyDot}`} />
          <span className="text-body text-ink-body">{proxyStateText}</span>
        </span>
        {account.proxy_id ? (
          <Button
            size="xs"
            className="text-ink-muted"
            onClick={onUnassign}
            loading={unassignProxy.isPending}
          >
            {unassignProxy.isPending ? <Spinner size={12} /> : t('accounts.edit.proxyDetach')}
          </Button>
        ) : null}
      </div>
      {unassignProxy.isError ? (
        <div className="mb-md text-tiny text-danger">{t('accounts.edit.proxyDetachErr')}</div>
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
          <div className="mb-md">
            <proxyForm.Field name="host">
              {(field) => (
                <FormField field={field} label={t('accounts.edit.host')} className="font-mono" />
              )}
            </proxyForm.Field>
          </div>
          <div className="mb-md grid grid-cols-1 md:grid-cols-2 gap-md">
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
                <div>
                  <span className={LABEL}>{t('accounts.edit.type')}</span>
                  <Select
                    value={field.state.value}
                    onChange={(value) => {
                      field.handleChange(value as ProxyFormValue['proxy_type']);
                    }}
                    options={PROXY_TYPES}
                    ariaLabel={t('accounts.edit.type')}
                  />
                </div>
              )}
            </proxyForm.Field>
          </div>
          <div className="mb-lg grid grid-cols-1 md:grid-cols-2 gap-md">
            <proxyForm.Field name="username">
              {/* FormField emits name="username" — next to a password input that
                  is the formless login shape Chrome's password parser matches,
                  so both halves opt out of autofill (and of the generation
                  bubble `new-password` would summon on a username neighbour). */}
              {(field) => (
                <FormField field={field} label={t('accounts.edit.login')} autoComplete="off" />
              )}
            </proxyForm.Field>
            <proxyForm.Field name="password">
              {(field) => (
                <label className="block">
                  <span className={LABEL}>{t('accounts.edit.password')}</span>
                  <div className="relative">
                    <Input
                      className="pr-[36px]"
                      value={field.state.value}
                      onChange={(event) => {
                        field.handleChange(event.target.value);
                      }}
                      onBlur={field.handleBlur}
                      type={showPass ? 'text' : 'password'}
                      autoComplete="new-password"
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
        <div className="mb-lg">
          <span className={LABEL}>{t('accounts.proxyPool.title')}</span>
          <Select
            value={account.proxy_id ?? ''}
            disabled={proxyBusy}
            onChange={assignFromPool}
            options={poolChoices.map((proxy) => ({
              value: proxy.id,
              label: `${proxy.host}:${String(proxy.port)}`,
            }))}
            placeholder={t('accounts.edit.choosePoolProxy')}
            ariaLabel={t('accounts.proxyPool.title')}
          />
        </div>
      )}
      <div className="flex flex-wrap items-center gap-md">
        <Button
          size="sm"
          className="items-center gap-sm"
          onClick={onProxyAction}
          disabled={proxyBusy || (proxyMode === 'manual' && !proxyFormCanSubmit)}
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
          {proxyMode === 'manual'
            ? t('accounts.edit.proxyAddAssign')
            : t('accounts.edit.proxyCheck')}
        </Button>
        {proxyCheck === 'loading' && (
          <span className="text-body text-ink-subtle">{t('accounts.edit.proxyChecking')}</span>
        )}
        {proxyCheck === 'ok' && (
          <Badge tone="success" size="md" className="tb-pop gap-sm">
            {proxyResult?.country_code ? (
              <span
                className={`fi fi-${proxyResult.country_code.toLowerCase()} inline-block h-[13px] w-[18px] rounded-[2px] shadow-ring`}
              />
            ) : null}
            {[proxyResult?.country_code?.toUpperCase(), proxyResult?.exit_ip]
              .filter(Boolean)
              .join(' · ') || t('accounts.edit.proxyReachable')}
          </Badge>
        )}
        {proxyCheck === 'err' && (
          <span className="inline-flex items-center gap-sm text-body font-medium text-danger">
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
      {confirmReplace ? (
        <ConfirmModal
          title={t('accounts.edit.proxyReplaceTitle')}
          body={t('accounts.edit.proxyReplaceBody')}
          confirmLabel={t('accounts.edit.proxyReplaceConfirm')}
          cancelLabel={t('accounts.edit.cancel')}
          onClose={() => {
            setConfirmReplace(false);
          }}
          onConfirm={() => {
            void proxyForm.handleSubmit();
          }}
        />
      ) : null}
    </Section>
  );
}
