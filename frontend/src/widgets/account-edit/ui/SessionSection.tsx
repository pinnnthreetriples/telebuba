import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useRef, useState, type ChangeEvent } from 'react';
import { useTranslation } from 'react-i18next';

import {
  accountHealth,
  importAccountSessionMutation,
  importAccountTdataMutation,
  invalidateAccountViews,
  logoutAccountMutation,
  requestLoginCodeMutation,
  submitLoginCodeMutation,
} from '@/entities/account';
import type { AccountRead } from '@/shared/api';
import { useClearedTimeouts } from '@/shared/lib';
import { FeedbackMark } from '@/shared/ui';

import { Section, Spinner } from './_shared';
import { FIELD, LABEL, SEG_WRAP, seg, type CheckState } from './_styles';

// Session-state dot colour keyed on the backend health (ok/warn/fail), so the
// card shows the real session state — not a hardcoded green "active".
const HEALTH_DOT: Record<ReturnType<typeof accountHealth>, string> = {
  ok: '#2e9e64',
  warn: '#e08700',
  fail: '#c0473f',
};

// One queued/finished import in the dropzone's file list. `id` is per enqueue,
// not the filename: two imports of the same file share a name, and settling by
// name flipped both entries to "готово" when the first one finished.
interface Upload {
  id: number;
  name: string;
  archive: boolean;
  status: 'uploading' | 'done' | 'error';
}

// Session card: real session-state row + logout, phone-code login, and the
// .session/tdata.zip import dropzone (the dropzone is presentational, #6).
export function SessionSection({ account }: { account: AccountRead }) {
  const { t } = useTranslation();
  const [importTab, setImportTab] = useState<'session' | 'tdata'>('session');
  const [uploads, setUploads] = useState<Upload[]>([]);
  const uploadInput = useRef<HTMLInputElement>(null);
  const nextUploadId = useRef(0);
  const [logoutCheck, setLogoutCheck] = useState<CheckState>('idle');
  const [smsCode, setSmsCode] = useState('');
  const [twoFa, setTwoFa] = useState('');
  const [loginNote, setLoginNote] = useState<string | null>(null);

  const queryClient = useQueryClient();
  const later = useClearedTimeouts();
  const importTdata = useMutation(importAccountTdataMutation());
  const importSession = useMutation(importAccountSessionMutation());
  const requestCode = useMutation(requestLoginCodeMutation());
  const submitCode = useMutation(submitLoginCodeMutation());
  const logout = useMutation(logoutAccountMutation());
  const invalidate = () => {
    invalidateAccountViews(queryClient);
  };

  const path = { path: { account_id: account.account_id } } as const;
  // The field's "1 2 3 4 5" placeholder and letter-spacing invite a
  // space-separated code, which Telegram rejects verbatim.
  const code = smsCode.trim();
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
      { ...path, body: { code, password: twoFa || null } },
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
  const onLogout = () => {
    setLogoutCheck('loading');
    logout.mutate(path, {
      onSuccess: () => {
        setLogoutCheck('ok');
        invalidate();
      },
      onError: () => {
        setLogoutCheck('err');
      },
      onSettled: () => {
        later(() => {
          setLogoutCheck('idle');
        }, 1600);
      },
    });
  };

  // Import a .session / tdata.zip file as a new account (the active import tab
  // picks the endpoint); the file card tracks uploading → done | error.
  const onUploadFile = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    const { name } = file;
    const archive = importTab === 'tdata';
    const id = (nextUploadId.current += 1);
    setUploads((list) => [{ id, name, archive, status: 'uploading' }, ...list]);
    const settle = (status: Upload['status']) => {
      setUploads((list) => list.map((item) => (item.id === id ? { ...item, status } : item)));
      if (status === 'done') invalidate();
    };
    // `mutateAsync`, NOT `mutate` with mutate-level callbacks: both imports share
    // one useMutation, and the observer holds exactly ONE callback set — a second
    // `mutate` overwrites `#mutateOptions` and detaches the observer from the
    // first mutation (mutationObserver.ts: "there is no way to get it back"), so
    // picking a second file before the first resolved left card #1 on
    // «загрузка…» forever. The promise `mutateAsync` returns belongs to THIS
    // mutation and survives both.
    const upload = archive
      ? importTdata.mutateAsync({ body: { file } })
      : importSession.mutateAsync({ body: { file } });
    void upload.then(
      () => {
        settle('done');
      },
      () => {
        settle('error');
      },
    );
    event.target.value = '';
  };

  // Real session-state row: green "active" only when the session is actually
  // alive; otherwise the matching health colour + a session-scoped inactive label.
  const sessionDot = HEALTH_DOT[accountHealth(account.status)];
  const sessionText =
    account.status === 'alive' ? t('accounts.edit.sessionOk') : t('accounts.edit.sessionInactive');

  return (
    <Section title={t('accounts.edit.session')}>
      <div className="mb-[10px] flex items-center justify-between gap-[10px] rounded-[10px] bg-[#f6f5f2] px-3 py-[10px]">
        <span className="flex items-center gap-2">
          <span className="h-2 w-2 rounded-full" style={{ background: sessionDot }} />
          <span className="text-[12.5px] text-[#3a3a3a]">{sessionText}</span>
        </span>
        <span className="flex items-center gap-[7px]">
          <FeedbackMark
            result={logoutCheck === 'idle' || logoutCheck === 'loading' ? undefined : logoutCheck}
          />
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
            // The account's 2FA password, never the operator's. `new-password`
            // is the only token browsers honour as "do not fill" on a password
            // input (`off` is documented as ignored), and `current-password`
            // would invite the dashboard credential straight into this box —
            // which onConfirmLogin then POSTs as the account's 2FA password.
            autoComplete="new-password"
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
        disabled={submitCode.isPending || !code}
        className="w-full rounded-[10px] border border-line-input bg-white py-[9px] text-[13px] font-medium disabled:opacity-50"
      >
        {submitCode.isPending ? <Spinner size={14} /> : t('accounts.edit.confirmLogin')}
      </button>
      {loginNote ? <div className="mt-[8px] text-[11.5px] text-ink-muted">{loginNote}</div> : null}
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
        {uploads.map((file) => (
          <div
            key={file.id}
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
                        setUploads((list) => list.filter((item) => item.id !== file.id));
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
  );
}
