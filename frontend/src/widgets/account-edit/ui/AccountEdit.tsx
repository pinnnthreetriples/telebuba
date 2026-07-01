import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useRef, useState, type ChangeEvent, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import {
  checkAccountMutation,
  deleteAccountMutation,
  importAccountSessionMutation,
  importAccountTdataMutation,
  logoutAccountMutation,
  requestLoginCodeMutation,
  resetAccountSessionMutation,
  spamCheckAccountMutation,
  StatusBadge,
  submitLoginCodeMutation,
} from '@/entities/account';
import {
  assignProxyMutation,
  checkProxyMutation,
  createProxyMutation,
  proxyPoolQueryOptions,
} from '@/entities/proxy';
import type { AccountRead } from '@/shared/api';
import { FeedbackMark, Modal } from '@/shared/ui';

import { EMPTY_PROXY_FORM, type ProxyFormValue } from './proxyFormValue';

const FIELD =
  'tb-time w-full rounded-[10px] border border-line-input bg-white px-3 py-[9px] text-[13px] outline-none';
const FIELD_LOCKED =
  'w-full cursor-not-allowed rounded-[10px] border border-line bg-[#f6f5f2] px-3 py-[9px] text-[13px] text-ink-subtle outline-none';
const LABEL = 'mb-[6px] block text-[12px] font-medium text-[#3a3a3a]';
const SEG_WRAP = 'mb-[10px] flex gap-1 rounded-[10px] bg-[#f1efed] p-1';
const seg = (on: boolean): string =>
  `flex-1 rounded-[7px] py-[7px] text-[12.5px] font-medium transition ${on ? 'bg-white text-ink shadow-sm' : 'text-ink-muted'}`;

// A check-button drives a tiny idle→loading→(ok|err) machine, settling back to
// idle. Backed by real check calls (proxy connectivity / @SpamBot / alive).
type CheckState = 'idle' | 'loading' | 'ok' | 'err';

// Real spam-status dot per verdict (matches the design's traffic-light tints).
const SPAM_DOT: Record<NonNullable<AccountRead['spam_status']>, string> = {
  clean: 'bg-[#2e9e64]',
  limited: 'bg-[#c0473f]',
  unknown: 'bg-line-strong',
};

// One queued/finished import in the dropzone's file list.
interface Upload {
  name: string;
  archive: boolean;
  status: 'uploading' | 'done' | 'error';
}

function mono(account: AccountRead): string {
  return (account.phone ?? account.account_id).replace(/\D/g, '').slice(-2) || '#';
}

// Trust Score is real (computed by the backend); the 3-tier colour band mirrors
// the design's thresholds.
function trustColor(t: number): string {
  return t >= 70 ? '#12a150' : t >= 45 ? '#e08700' : '#e5372a';
}

function Spinner({ size }: { size: number }) {
  return (
    <span
      className="tb-spin inline-block rounded-full border-2 border-[#c8c6c2] border-t-primary"
      style={{ width: size, height: size }}
    />
  );
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
// actions. All wired to /api/v1 (proxy pool/manual assign, phone-code login,
// checks, profile); only the .session/tdata dropzone is presentational (#6).
export function AccountEdit({ account, onBack }: { account: AccountRead; onBack: () => void }) {
  const { t } = useTranslation();
  const [importTab, setImportTab] = useState<'session' | 'tdata'>('session');
  const [proxyMode, setProxyMode] = useState<'pool' | 'manual'>('manual');
  const [proxyForm, setProxyForm] = useState<ProxyFormValue>(EMPTY_PROXY_FORM);
  const [uploads, setUploads] = useState<Upload[]>([]);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const uploadInput = useRef<HTMLInputElement>(null);
  const [showPass, setShowPass] = useState(false);
  const [proxyCheck, setProxyCheck] = useState<CheckState>('idle');
  const [spamCheck, setSpamCheck] = useState<CheckState>('idle');
  const [aliveCheck, setAliveCheck] = useState<CheckState>('idle');
  const [logoutCheck, setLogoutCheck] = useState<CheckState>('idle');
  const [resetCheck, setResetCheck] = useState<CheckState>('idle');
  const [smsCode, setSmsCode] = useState('');
  const [twoFa, setTwoFa] = useState('');
  const [loginNote, setLoginNote] = useState<string | null>(null);

  const queryClient = useQueryClient();
  const proxyMutation = useMutation(checkProxyMutation());
  const createProxy = useMutation(createProxyMutation());
  const assignProxy = useMutation(assignProxyMutation());
  const importTdata = useMutation(importAccountTdataMutation());
  const importSession = useMutation(importAccountSessionMutation());
  const deleteAccount = useMutation(deleteAccountMutation());
  const pool = useQuery(proxyPoolQueryOptions());
  const freeProxies = (pool.data?.proxies ?? []).filter((proxy) => proxy.free > 0);
  const spamMutation = useMutation(spamCheckAccountMutation());
  const aliveMutation = useMutation(checkAccountMutation());
  const requestCode = useMutation(requestLoginCodeMutation());
  const submitCode = useMutation(submitLoginCodeMutation());
  const logout = useMutation(logoutAccountMutation());
  const resetSession = useMutation(resetAccountSessionMutation());
  const invalidate = () => {
    void queryClient.invalidateQueries();
  };

  const path = { path: { account_id: account.account_id } } as const;
  const onRequestCode = () => {
    setLoginNote(null);
    requestCode.mutate(path, {
      onSuccess: (result) => {
        setLoginNote(t('accounts.edit.codeSent', { phone: result.phone }));
      },
      onError: () => {
        setLoginNote(t('accounts.edit.codeError'));
      },
    });
  };
  const onConfirmLogin = () => {
    setLoginNote(null);
    submitCode.mutate(
      { ...path, body: { code: smsCode, password: twoFa || null } },
      {
        onSuccess: () => {
          setSmsCode('');
          setTwoFa('');
          setLoginNote(t('accounts.edit.loginOk'));
          invalidate();
        },
        onError: () => {
          setLoginNote(t('accounts.edit.loginErr'));
        },
      },
    );
  };
  // Shared shape behind the logout/reset buttons: loading → ok/err → idle.
  const runSessionAction = (
    mutation: typeof logout | typeof resetSession,
    setCheck: typeof setLogoutCheck,
  ) => {
    setCheck('loading');
    mutation.mutate(path, {
      onSuccess: () => {
        setCheck('ok');
        invalidate();
      },
      onError: () => {
        setCheck('err');
      },
      onSettled: () => {
        window.setTimeout(() => {
          setCheck('idle');
        }, 1600);
      },
    });
  };
  const onLogout = () => {
    runSessionAction(logout, setLogoutCheck);
  };
  const onReset = () => {
    runSessionAction(resetSession, setResetCheck);
  };
  const onDelete = () => {
    deleteAccount.mutate(path, {
      onSuccess: () => {
        invalidate();
        onBack();
      },
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

  // Manual mode: create the entered proxy (idempotent), assign it, verify it.
  const addManualProxy = () => {
    setProxyCheck('loading');
    createProxy.mutate(
      {
        body: {
          proxy_type: proxyForm.proxy_type,
          host: proxyForm.host.trim(),
          port: Number(proxyForm.port),
          username: proxyForm.username.trim() || null,
          password: proxyForm.password || null,
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
    if (proxyMode === 'manual') addManualProxy();
    else runProxyCheck();
  };

  // Import a .session / tdata.zip file as a new account (the active import tab
  // picks the endpoint); the file card tracks uploading → done | error.
  const onUploadFile = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    const { name } = file;
    const archive = importTab === 'tdata';
    setUploads((list) => [{ name, archive, status: 'uploading' }, ...list]);
    const settle = (status: Upload['status']) => {
      setUploads((list) => list.map((item) => (item.name === name ? { ...item, status } : item)));
      if (status === 'done') invalidate();
    };
    const handlers = {
      onSuccess: () => {
        settle('done');
      },
      onError: () => {
        settle('error');
      },
    };
    if (archive) importTdata.mutate({ body: { file } }, handlers);
    else importSession.mutate({ body: { file } }, handlers);
    event.target.value = '';
  };

  // Real @SpamBot probe; the result also refreshes the signals on next load.
  const runSpamCheck = () => {
    setSpamCheck('loading');
    spamMutation.mutate(
      { path: { account_id: account.account_id } },
      {
        onSuccess: (verdict) => {
          setSpamCheck(verdict.status === 'clean' ? 'ok' : 'err');
          window.setTimeout(() => {
            setSpamCheck('idle');
          }, 2400);
          invalidate();
        },
        onError: () => {
          setSpamCheck('err');
        },
      },
    );
  };

  // Real liveness check (reuses the accounts-table «Проверить» endpoint).
  const runAliveCheck = () => {
    setAliveCheck('loading');
    aliveMutation.mutate(
      { body: { account_id: account.account_id } },
      {
        onSuccess: (checked) => {
          setAliveCheck(checked.status === 'alive' ? 'ok' : 'err');
          window.setTimeout(() => {
            setAliveCheck('idle');
          }, 2400);
          invalidate();
        },
        onError: () => {
          setAliveCheck('err');
        },
      },
    );
  };

  const trust = account.trust_score ?? 0;
  const tColor = trustColor(trust);
  const country = account.proxy_country_code?.toUpperCase() ?? '—';

  // Real spam/ban signals, sourced from the account's last cached @SpamBot verdict
  // + last liveness check (read-only — refreshed by the «Спам-чек» button).
  const spamStatus = account.spam_status;
  const signals = [
    {
      dot: spamStatus ? SPAM_DOT[spamStatus] : 'bg-line-strong',
      label: t('accounts.edit.signalStatus'),
      value: t(`accounts.edit.spam.${spamStatus ?? 'unknown'}`),
    },
    {
      dot: spamStatus === 'limited' ? SPAM_DOT.limited : 'bg-line-strong',
      label: t('accounts.edit.signalBlock'),
      value:
        spamStatus === 'limited'
          ? (account.spam_detail ?? t('accounts.edit.signalRecorded'))
          : t('accounts.edit.signalNone'),
    },
    {
      dot: account.last_checked_at ? 'bg-[#2e9e64]' : 'bg-line-strong',
      label: t('accounts.edit.signalChecked'),
      value: account.last_checked_at
        ? account.last_checked_at.slice(0, 10)
        : t('accounts.edit.signalNever'),
    },
  ];

  return (
    <div className="tb-fadeup max-w-[960px]">
      <button
        type="button"
        onClick={onBack}
        className="mb-4 inline-flex items-center gap-[6px] bg-transparent p-0 text-[13px] font-medium text-ink-muted hover:text-ink"
      >
        ← {t('accounts.edit.back')}
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
            <span className="text-[18px] font-bold" style={{ color: tColor }}>
              {trust}/100
            </span>
          </div>
          <div className="mt-[6px] h-[6px] overflow-hidden rounded-full bg-track">
            <div
              className="h-full rounded-full"
              style={{ width: `${String(trust)}%`, background: tColor }}
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
            <span className="flex items-center gap-[7px]">
              <FeedbackMark result={logoutCheck === 'idle' || logoutCheck === 'loading' ? undefined : logoutCheck} />
              <button
                type="button"
                onClick={onLogout}
                disabled={logout.isPending}
                className="rounded-[8px] border border-line-input bg-white px-3 py-[5px] text-[12px] font-medium text-ink-muted disabled:opacity-50"
              >
                {logoutCheck === 'loading' ? <Spinner size={12} /> : t('accounts.edit.logout')}
              </button>
            </span>
          </div>
          <div className="mb-[9px] mt-4 flex items-center justify-between gap-2">
            <span className="text-[11px] font-semibold uppercase tracking-[0.04em] text-ink-subtle">
              {t('accounts.edit.loginByCode')}
            </span>
            <button
              type="button"
              onClick={onRequestCode}
              disabled={requestCode.isPending}
              className="rounded-full border border-line-input bg-white px-3 py-[4px] text-[11.5px] font-medium text-primary disabled:opacity-50"
            >
              {requestCode.isPending ? <Spinner size={12} /> : t('accounts.edit.sendCode')}
            </button>
          </div>
          <div className="mb-[9px] grid grid-cols-2 gap-[10px]">
            <label>
              <span className={LABEL}>{t('accounts.edit.smsCode')}</span>
              <input
                value={smsCode}
                onChange={(event) => {
                  setSmsCode(event.target.value);
                }}
                placeholder="1 2 3 4 5"
                className={`${FIELD} tracking-[0.18em]`}
              />
            </label>
            <label>
              <span className={LABEL}>{t('accounts.edit.twoFA')}</span>
              <input
                type="password"
                value={twoFa}
                onChange={(event) => {
                  setTwoFa(event.target.value);
                }}
                placeholder="••••••"
                className={FIELD}
              />
            </label>
          </div>
          <button
            type="button"
            onClick={onConfirmLogin}
            disabled={submitCode.isPending || !smsCode}
            className="w-full rounded-[10px] border border-line-input bg-white py-[9px] text-[13px] font-medium disabled:opacity-50"
          >
            {submitCode.isPending ? <Spinner size={14} /> : t('accounts.edit.confirmLogin')}
          </button>
          {loginNote ? (
            <div className="mt-[8px] text-[11.5px] text-ink-muted">{loginNote}</div>
          ) : null}
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
          <button
            type="button"
            onClick={() => uploadInput.current?.click()}
            className="flex w-full items-center gap-[11px] rounded-[12px] border border-dashed border-line bg-canvas/40 px-4 py-[14px] text-left"
          >
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
          </button>
          <input
            ref={uploadInput}
            type="file"
            accept={importTab === 'tdata' ? '.zip' : '.session'}
            className="hidden"
            onChange={onUploadFile}
          />
          <div className="mt-[9px] flex flex-col gap-2">
            {uploads.map((file, index) => (
              <div
                key={`${file.name}-${String(index)}`}
                className="tb-fadeup rounded-[11px] border border-line bg-white px-[11px] py-[10px]"
              >
                <div className="flex items-center gap-[10px]">
                  <div className="flex h-[34px] w-[34px] shrink-0 items-center justify-center rounded-[9px] bg-[#f4f3f0] text-ink-muted">
                    {file.archive ? (
                      <svg
                        width="16"
                        height="16"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="1.7"
                      >
                        <rect x="3" y="3" width="18" height="18" rx="2" />
                        <path d="M12 7v2M12 12v2M12 17v.5" />
                      </svg>
                    ) : (
                      <svg
                        width="16"
                        height="16"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="1.7"
                      >
                        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                        <path d="M14 2v6h6" />
                      </svg>
                    )}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <div className="truncate text-[12px] font-semibold">{file.name}</div>
                        <div className="mt-px text-[10.5px] text-ink-subtle">
                          {t(`accounts.edit.upload.${file.status}`)}
                        </div>
                      </div>
                      <div className="flex shrink-0 items-center gap-[2px]">
                        {file.status === 'done' ? (
                          <span className="tb-pop m-[3px] inline-flex text-[#2e9e64]">
                            <svg
                              width="17"
                              height="17"
                              viewBox="0 0 24 24"
                              fill="none"
                              stroke="currentColor"
                              strokeWidth="2"
                            >
                              <circle cx="12" cy="12" r="10" />
                              <path d="m8 12 2.5 2.5L16 9" />
                            </svg>
                          </span>
                        ) : file.status === 'error' ? (
                          <span className="m-[3px] inline-flex text-[#c0473f]">
                            <svg
                              width="17"
                              height="17"
                              viewBox="0 0 24 24"
                              fill="none"
                              stroke="currentColor"
                              strokeWidth="2"
                            >
                              <circle cx="12" cy="12" r="10" />
                              <path d="m15 9-6 6M9 9l6 6" />
                            </svg>
                          </span>
                        ) : (
                          <Spinner size={13} />
                        )}
                        <button
                          type="button"
                          aria-label={t('accounts.edit.removeFile')}
                          onClick={() => {
                            setUploads((list) => list.filter((_, position) => position !== index));
                          }}
                          className="inline-flex h-[25px] w-[25px] items-center justify-center rounded-full text-ink-subtle"
                        >
                          <svg
                            width="13"
                            height="13"
                            viewBox="0 0 24 24"
                            fill="none"
                            stroke="currentColor"
                            strokeWidth="2"
                          >
                            <path d="M18 6 6 18M6 6l12 12" />
                          </svg>
                        </button>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </Section>

        <Section title={t('accounts.edit.proxy')}>
          <div className="mb-3 text-[12px] text-ink-subtle">{t('accounts.edit.proxyRequired')}</div>
          <div className="mb-3 flex items-center gap-2 rounded-[10px] bg-[#f6f5f2] px-3 py-[10px]">
            <span className="h-2 w-2 rounded-full bg-[#2e9e64]" />
            <span className="text-[12.5px] text-[#3a3a3a]">
              {t('accounts.edit.proxyOk')} · {country}
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
                <input
                  value={proxyForm.host}
                  onChange={(event) => {
                    setProxyForm((value) => ({ ...value, host: event.target.value }));
                  }}
                  className={`${FIELD} font-mono`}
                />
              </label>
              <div className="mb-[10px] grid grid-cols-2 gap-[10px]">
                <label>
                  <span className={LABEL}>{t('accounts.edit.port')}</span>
                  <input
                    value={proxyForm.port}
                    inputMode="numeric"
                    onChange={(event) => {
                      setProxyForm((value) => ({
                        ...value,
                        port: event.target.value.replace(/\D/g, ''),
                      }));
                    }}
                    className={`${FIELD} font-mono`}
                  />
                </label>
                <label>
                  <span className={LABEL}>{t('accounts.edit.type')}</span>
                  <select
                    value={proxyForm.proxy_type}
                    onChange={(event) => {
                      setProxyForm((value) => ({
                        ...value,
                        proxy_type: event.target.value as ProxyFormValue['proxy_type'],
                      }));
                    }}
                    className={FIELD}
                  >
                    <option value="socks5">SOCKS5</option>
                    <option value="https">HTTPS</option>
                  </select>
                </label>
              </div>
              <div className="mb-[14px] grid grid-cols-2 gap-[10px]">
                <label>
                  <span className={LABEL}>{t('accounts.edit.login')}</span>
                  <input
                    value={proxyForm.username}
                    onChange={(event) => {
                      setProxyForm((value) => ({ ...value, username: event.target.value }));
                    }}
                    className={FIELD}
                  />
                </label>
                <label>
                  <span className={LABEL}>{t('accounts.edit.password')}</span>
                  <div className="relative">
                    <input
                      value={proxyForm.password}
                      onChange={(event) => {
                        setProxyForm((value) => ({ ...value, password: event.target.value }));
                      }}
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
              className="inline-flex items-center gap-[7px] rounded-full border border-line-input bg-white px-4 py-2 text-[13px] font-medium"
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
              <span className="text-[12.5px] text-ink-subtle">
                {t('accounts.edit.proxyChecking')}
              </span>
            )}
            {proxyCheck === 'ok' && (
              <span className="tb-pop inline-flex items-center gap-[6px] rounded-full bg-[#e7f2ec] px-3 py-[5px] text-[12.5px] font-medium text-[#2e7d55]">
                <span className="inline-block h-[13px] w-[18px] rounded-[2px] bg-[#21468b] shadow-[0_0_0_1px_rgba(0,0,0,.07)]" />
                {country} · 12ms
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
              <input value={account.device_model ?? '—'} disabled className={FIELD_LOCKED} />
            </label>
            <label>
              <span className={LABEL}>{t('accounts.edit.deviceOs')}</span>
              <input
                value={account.device_system_version ?? '—'}
                disabled
                className={FIELD_LOCKED}
              />
            </label>
            <label>
              <span className={LABEL}>{t('accounts.edit.deviceLang')}</span>
              <input value={account.device_lang ?? '—'} disabled className={FIELD_LOCKED} />
            </label>
          </div>
        </Section>

        <Section
          title={t('accounts.edit.signals')}
          right={
            <span className="tb-tip">
              <button
                type="button"
                onClick={runSpamCheck}
                className={`inline-flex items-center gap-[6px] rounded-full px-3 py-[5px] text-[12px] font-medium transition-[background-color,border-color,color] duration-300 ${
                  spamCheck === 'ok'
                    ? 'border border-[#2e9e64] bg-[#2e9e64] text-white'
                    : spamCheck === 'err'
                      ? 'border border-[#c0473f] bg-[#c0473f] text-white'
                      : 'border border-line-input bg-white text-ink-muted'
                }`}
              >
                {spamCheck === 'loading' && <Spinner size={13} />}
                {spamCheck === 'ok' && (
                  <span className="tb-blur inline-flex">
                    <svg
                      width="14"
                      height="14"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="#fff"
                      strokeWidth="2.6"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    >
                      <path d="M20 6 9 17l-5-5" />
                    </svg>
                  </span>
                )}
                {spamCheck === 'err' && (
                  <span className="tb-blur inline-flex">
                    <svg
                      width="14"
                      height="14"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="#fff"
                      strokeWidth="2.6"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    >
                      <path d="M18 6 6 18" />
                      <path d="m6 6 12 12" />
                    </svg>
                  </span>
                )}
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
            {signals.map((signal) => (
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
            <div
              className="mt-px text-[11.5px]"
              style={{
                color:
                  aliveCheck === 'ok' ? '#2e9e64' : aliveCheck === 'err' ? '#c0473f' : '#9a9893',
              }}
            >
              {aliveCheck === 'ok'
                ? t('accounts.edit.aliveOk')
                : aliveCheck === 'err'
                  ? t('accounts.edit.aliveErr')
                  : t('accounts.edit.aliveHint')}
            </div>
          </div>
          <button
            type="button"
            onClick={runAliveCheck}
            title={t('accounts.edit.aliveBtnTitle')}
            aria-label={t('accounts.edit.aliveBtnTitle')}
            className="flex h-[30px] w-[30px] shrink-0 items-center justify-center rounded-full border transition-[background-color,border-color,color] duration-300"
            style={{
              borderColor:
                aliveCheck === 'ok' ? '#2e9e64' : aliveCheck === 'err' ? '#c0473f' : '#e6e5e3',
              background:
                aliveCheck === 'ok' ? '#2e9e64' : aliveCheck === 'err' ? '#c0473f' : '#fff',
              color: aliveCheck === 'ok' || aliveCheck === 'err' ? '#fff' : '#74726e',
            }}
          >
            {aliveCheck === 'idle' && (
              <span className="tb-blur inline-flex">
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
              </span>
            )}
            {aliveCheck === 'loading' && <Spinner size={15} />}
            {aliveCheck === 'ok' && (
              <span className="tb-blur inline-flex">
                <svg
                  width="18"
                  height="18"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.4"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <path d="M20 6 9 17l-5-5" />
                </svg>
              </span>
            )}
            {aliveCheck === 'err' && (
              <span className="tb-blur inline-flex">
                <svg
                  width="18"
                  height="18"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.4"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <path d="M18 6 6 18" />
                  <path d="m6 6 12 12" />
                </svg>
              </span>
            )}
          </button>
        </div>
        <div className="flex items-center justify-between gap-3 border-b border-[#f0eeeb] py-[14px]">
          <div>
            <div className="text-[13px] font-medium">{t('accounts.edit.resetSession')}</div>
            <div className="mt-px text-[11.5px] text-ink-subtle">
              {t('accounts.edit.resetSessionHint')}
            </div>
          </div>
          <span className="flex shrink-0 items-center gap-[7px]">
            <FeedbackMark result={resetCheck === 'idle' || resetCheck === 'loading' ? undefined : resetCheck} />
            <button
              type="button"
              onClick={onReset}
              disabled={resetSession.isPending}
              className="rounded-full border border-line-input bg-white px-4 py-2 text-[13px] font-medium disabled:opacity-50"
            >
              {resetCheck === 'loading' ? <Spinner size={14} /> : t('accounts.edit.reset')}
            </button>
          </span>
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
            onClick={() => {
              setConfirmDelete(true);
            }}
            className="shrink-0 px-1 py-2 text-[13px] font-medium text-[#c0473f]"
          >
            {t('accounts.edit.deleteAccount')}
          </button>
        </div>
      </Section>

      {confirmDelete ? (
        <Modal
          onClose={() => {
            setConfirmDelete(false);
          }}
          z={70}
          className="w-[420px]"
        >
          <div className="p-6">
            <div className="mb-2 text-[16px] font-bold">
              {t('accounts.deleteModal.title', { phone: account.phone ?? account.account_id })}
            </div>
            <div className="mb-[22px] text-[13px] leading-[1.5] text-ink-muted">
              {t('accounts.deleteModal.body')}
            </div>
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={() => {
                  setConfirmDelete(false);
                }}
                className="rounded-full border border-line-input bg-white px-[18px] py-[9px] text-[13px] font-medium text-ink"
              >
                {t('accounts.deleteModal.cancel')}
              </button>
              <button
                type="button"
                onClick={() => {
                  setConfirmDelete(false);
                  onDelete();
                }}
                className="rounded-full border border-[#f0c9c5] bg-danger-tint px-5 py-[9px] text-[13px] font-semibold text-danger"
              >
                {t('accounts.deleteModal.confirm')}
              </button>
            </div>
          </div>
        </Modal>
      ) : null}
    </div>
  );
}
