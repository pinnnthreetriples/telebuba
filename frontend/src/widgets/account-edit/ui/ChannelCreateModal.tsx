import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  accountChannelsQueryKey,
  accountChannelUsernameCheckQueryOptions,
  createAccountChannelMutation,
} from '@/entities/account';
import { Modal } from '@/shared/ui';

import {
  CHANNEL_ABOUT_MAX,
  CHANNEL_TITLE_MAX,
  CHANNEL_USERNAME_RE,
  channelErrorText,
  errorChannelId,
  FIELD,
  LABEL,
} from './_channelsShared';

// New-channel dialog (opened above the profile modal, z=75): title + about +
// an optional public username with a debounced live availability check.
// On success the caller gets the created channel's id (ActionResult.channel_id)
// so it can jump straight into the editor.
const CHECK_DEBOUNCE_MS = 500;

export function ChannelCreateModal({
  accountId,
  onClose,
  onCreated,
}: {
  accountId: string;
  onClose: () => void;
  onCreated: (channelId: string | null) => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const create = useMutation(createAccountChannelMutation());
  const [title, setTitle] = useState('');
  const [about, setAbout] = useState('');
  const [isPublic, setIsPublic] = useState(false);
  const [username, setUsername] = useState('');
  // The availability probe hits Telegram — debounce it so typing doesn't fire
  // a request per keystroke.
  const [debounced, setDebounced] = useState('');
  useEffect(() => {
    const id = window.setTimeout(() => {
      setDebounced(username);
    }, CHECK_DEBOUNCE_MS);
    return () => {
      window.clearTimeout(id);
    };
  }, [username]);

  const usernameValid = CHANNEL_USERNAME_RE.test(username);
  const check = useQuery({
    ...accountChannelUsernameCheckQueryOptions({
      path: { account_id: accountId },
      query: { username: debounced },
    }),
    enabled: isPublic && CHANNEL_USERNAME_RE.test(debounced),
  });

  const busy = create.isPending;
  // `done` keeps the button locked after success while the caller closes the
  // dialog — a second click would create the SAME channel twice.
  const done = create.isSuccess;
  const canSubmit =
    !busy &&
    !done &&
    title.trim().length >= 1 &&
    title.trim().length <= CHANNEL_TITLE_MAX &&
    about.trim().length <= CHANNEL_ABOUT_MAX &&
    (!isPublic || usernameValid);

  const invalidateList = () =>
    queryClient.invalidateQueries({
      queryKey: accountChannelsQueryKey({ path: { account_id: accountId } }),
    });

  const submit = () => {
    if (!canSubmit) return;
    create.mutate(
      {
        path: { account_id: accountId },
        body: {
          title: title.trim(),
          about: about.trim(),
          username: isPublic ? username : null,
        },
      },
      {
        onSuccess: (result) => {
          void invalidateList();
          onCreated(result.channel_id ?? null);
        },
        onError: (err) => {
          // The channel may exist as private even though the request failed
          // (occupied username after a successful create) — its id rides the
          // envelope's fields, so the list must refresh either way.
          if (errorChannelId(err) !== null) void invalidateList();
        },
      },
    );
  };

  // Username hint line: format error → debounce/probe spinner → verdict.
  let usernameHint: { text: string; tone: 'muted' | 'ok' | 'error' } | null = null;
  if (isPublic) {
    if (username !== '' && !usernameValid) {
      usernameHint = { text: t('accounts.channel.errUsername'), tone: 'error' };
    } else if (usernameValid && (username !== debounced || check.isFetching)) {
      usernameHint = { text: t('accounts.channel.usernameChecking'), tone: 'muted' };
    } else if (usernameValid && check.data) {
      usernameHint = check.data.available
        ? { text: t('accounts.channel.usernameFree'), tone: 'ok' }
        : {
            text: t(`accounts.channel.code.${check.data.code ?? ''}`, {
              defaultValue: t('accounts.channel.usernameTaken'),
            }),
            tone: 'error',
          };
    }
  }
  const hintColor =
    usernameHint?.tone === 'ok'
      ? 'text-[#2e9e64]'
      : usernameHint?.tone === 'error'
        ? 'text-danger'
        : 'text-ink-subtle';

  return (
    // Escape / backdrop-click route through Modal's onClose — locked while the
    // create is in flight (unmounting mid-flight drops the onSuccess and loses
    // both the list refresh and the editor hand-off).
    <Modal onClose={busy ? () => undefined : onClose} z={75} backdrop={0.45} className="w-[460px]">
      <div className="tb-scroll max-h-[88vh] overflow-y-auto px-6 py-[22px]">
        <div className="mb-4 flex items-center justify-between">
          <span className="text-[16px] font-bold">{t('accounts.channel.createTitle')}</span>
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            aria-label={t('accounts.channel.close')}
            className="h-[30px] w-[30px] rounded-full border border-line bg-white text-[16px] text-ink-muted disabled:opacity-50"
          >
            ×
          </button>
        </div>

        <label className="mb-[14px] block">
          <span className={LABEL}>{t('accounts.channel.titleLabel')}</span>
          <input
            value={title}
            maxLength={CHANNEL_TITLE_MAX}
            onChange={(event) => {
              setTitle(event.target.value);
            }}
            className={FIELD}
          />
          {title !== '' && title.trim() === '' && (
            <span className="mt-1 block text-[11.5px] text-danger">
              {t('accounts.channel.errTitle')}
            </span>
          )}
        </label>

        <label className="mb-[14px] block">
          <span className={LABEL}>{t('accounts.channel.aboutLabel')}</span>
          <textarea
            rows={3}
            value={about}
            maxLength={CHANNEL_ABOUT_MAX}
            onChange={(event) => {
              setAbout(event.target.value);
            }}
            className={`${FIELD} resize-none [font-family:inherit]`}
          />
        </label>

        <button
          type="button"
          onClick={() => {
            setIsPublic((value) => !value);
          }}
          className="mb-[14px] flex w-full items-center gap-[10px] text-left"
        >
          <span
            className={`flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-[5px] border ${isPublic ? 'border-primary bg-primary' : 'border-line-input bg-white'}`}
          >
            {isPublic && (
              <svg
                width="13"
                height="13"
                viewBox="0 0 24 24"
                fill="none"
                stroke="#fff"
                strokeWidth="3"
              >
                <path d="M20 6 9 17l-5-5" />
              </svg>
            )}
          </span>
          <span className="text-[13px] text-[#3a3a3a]">{t('accounts.channel.publicToggle')}</span>
        </button>

        {isPublic && (
          <label className="mb-[14px] block">
            <span className={LABEL}>{t('accounts.channel.usernameLabel')}</span>
            <div className="relative flex items-center">
              <span className="absolute left-3 text-[13px] text-ink-subtle">@</span>
              <input
                value={username}
                onChange={(event) => {
                  setUsername(event.target.value);
                }}
                className={`${FIELD} pl-[26px]`}
              />
            </div>
            {usernameHint && (
              <span className={`mt-1 block text-[11.5px] ${hintColor}`}>{usernameHint.text}</span>
            )}
          </label>
        )}

        {create.isError && (
          <div className="mb-[14px] rounded-[10px] border border-[#f0c9c5] bg-danger-tint px-3 py-[10px] text-[12.5px] text-danger">
            {channelErrorText(create.error, t, t('accounts.channel.error'))}
          </div>
        )}

        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            className="rounded-full border border-line-input bg-white px-[18px] py-[9px] text-[13px] font-medium text-ink disabled:opacity-50"
          >
            {t('accounts.channel.cancel')}
          </button>
          <button
            type="button"
            onClick={submit}
            disabled={!canSubmit}
            className="rounded-full bg-primary px-5 py-[9px] text-[13px] font-medium text-white disabled:opacity-50"
          >
            {busy ? (
              <span className="inline-flex items-center gap-[6px]">
                <span className="tb-spin inline-block h-[14px] w-[14px] rounded-full border-2 border-white/40 border-t-white" />
                {t('accounts.channel.creating')}
              </span>
            ) : (
              t('accounts.channel.createBtn')
            )}
          </button>
        </div>
      </div>
    </Modal>
  );
}
