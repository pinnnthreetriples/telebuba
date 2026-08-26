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
import { Button, FeedbackMark, Icon, Input, SegmentedControl } from '@/shared/ui';

import { Section, Spinner } from './_shared';
import { LABEL, type CheckState } from './_styles';

// Session-state dot tone keyed on the backend health (ok/warn/fail), so the card
// shows the real session state — not a hardcoded green "active". Tokens, so the
// three tiers read the same as every other health signal.
const HEALTH_DOT: Record<ReturnType<typeof accountHealth>, string> = {
  ok: 'bg-success',
  warn: 'bg-warning-strong',
  fail: 'bg-danger',
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
      <div className="mb-md flex items-center justify-between gap-md rounded-lg bg-canvas px-md py-md">
        <span className="flex items-center gap-sm">
          <span className={`size-dot rounded-full ${sessionDot}`} />
          <span className="type-value">{sessionText}</span>
        </span>
        <span className="flex items-center gap-sm">
          <FeedbackMark
            result={logoutCheck === 'idle' || logoutCheck === 'loading' ? undefined : logoutCheck}
          />
          <Button
            size="xs"
            className="text-ink-muted"
            onClick={onLogout}
            loading={logout.isPending}
          >
            {logoutCheck === 'loading' ? <Spinner size={12} /> : t('accounts.edit.logout')}
          </Button>
        </span>
      </div>
      <div className="mb-md mt-lg flex items-center justify-between gap-sm">
        <span className="type-eyebrow">{t('accounts.edit.loginByCode')}</span>
        <button
          type="button"
          onClick={onRequestCode}
          disabled={requestCode.isPending}
          className="rounded-full border border-line bg-white px-md py-xs text-tiny font-medium text-primary disabled:opacity-50"
        >
          {requestCode.isPending ? <Spinner size={12} /> : t('accounts.edit.sendCode')}
        </button>
      </div>
      <div className="mb-md grid grid-cols-1 md:grid-cols-2 gap-md">
        <label>
          <span className={LABEL}>{t('accounts.edit.smsCode')}</span>
          <Input
            className="tracking-code"
            value={smsCode}
            onChange={(event) => {
              setSmsCode(event.target.value);
            }}
            placeholder="1 2 3 4 5"
          />
        </label>
        <label>
          <span className={LABEL}>{t('accounts.edit.twoFA')}</span>
          <Input
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
          />
        </label>
      </div>
      <Button size="block" onClick={onConfirmLogin} disabled={submitCode.isPending || !code}>
        {submitCode.isPending ? <Spinner size={14} /> : t('accounts.edit.confirmLogin')}
      </Button>
      {loginNote ? <div className="mt-sm type-caption">{loginNote}</div> : null}
      <div className="mb-md mt-xl type-eyebrow">{t('accounts.edit.import')}</div>
      <SegmentedControl
        className="mb-md"
        value={importTab}
        ariaLabel={t('accounts.edit.import')}
        options={(['session', 'tdata'] as const).map((tab) => ({
          value: tab,
          label: tab === 'session' ? '.session' : 'tdata.zip',
        }))}
        onChange={(tab) => {
          setImportTab(tab);
        }}
      />
      <button
        type="button"
        onClick={() => uploadInput.current?.click()}
        className="flex w-full items-center gap-md rounded-lg border border-dashed border-line bg-canvas/40 px-lg py-lg text-left"
      >
        <div className="flex size-thumbnail shrink-0 items-center justify-center rounded-lg border border-line bg-white text-primary">
          <Icon name="upload-cloud" size={20} />
        </div>
        <div className="min-w-0">
          <div className="type-item-title">{t('accounts.edit.dropTitle')}</div>
          <div className="mt-px type-caption">{t('accounts.edit.dropHint')}</div>
        </div>
      </button>
      <input
        ref={uploadInput}
        type="file"
        accept={importTab === 'tdata' ? '.zip' : '.session'}
        className="hidden"
        onChange={onUploadFile}
      />
      <div className="mt-md flex flex-col gap-sm">
        {uploads.map((file) => (
          <div
            key={file.id}
            className="tb-fadeup rounded-lg border border-line bg-white px-md py-md"
          >
            <div className="flex items-center gap-md">
              <div className="flex size-tile shrink-0 items-center justify-center rounded-md bg-canvas text-ink-muted">
                {file.archive ? (
                  <Icon name="alert-square" size={16} />
                ) : (
                  <Icon name="file" size={16} />
                )}
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-start justify-between gap-sm">
                  <div className="min-w-0">
                    <div className="truncate type-item-title">{file.name}</div>
                    <div className="mt-px type-meta">
                      {t(`accounts.edit.upload.${file.status}`)}
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-hair">
                    {file.status === 'done' ? (
                      <span className="tb-pop m-xs inline-flex text-success-deep">
                        <Icon name="check-circle" size={18} />
                      </span>
                    ) : file.status === 'error' ? (
                      <span className="m-xs inline-flex text-danger">
                        <Icon name="x-circle" size={18} />
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
                      className="inline-flex size-chip items-center justify-center rounded-full text-ink-subtle"
                    >
                      <Icon name="close" size={14} />
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
