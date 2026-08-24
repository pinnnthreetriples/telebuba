import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  accountChannelsQueryKey,
  accountChannelUsernameCheckQueryOptions,
  createAccountChannelMutation,
} from '@/entities/account';
import { Button, IconButton, Input, Modal, Notice, Textarea } from '@/shared/ui';

import {
  CHANNEL_ABOUT_MAX,
  CHANNEL_TITLE_MAX,
  CHANNEL_USERNAME_RE,
  channelErrorText,
  envelopeMessage,
  errorChannelId,
  LABEL,
} from './_channelsShared';
import { CheckRow } from './_CheckRow';

// New-channel dialog (opened above the profile modal, z=75): title + about +
// an optional public username with a debounced live availability check.
// On success the caller gets the created channel's id (ActionResult.channel_id)
// so it can jump straight into the editor.
const CHECK_DEBOUNCE_MS = 500;

// The ONLY create refusals that can arrive with the channel already made and no
// id to hand off, read off `_create_channel` (core/telegram_client/_channels.py):
// FloodWaitError/PeerFloodError are re-raised bare from the post-create username
// assignment and surface as the ActionStatus itself, and `channel_create_failed`
// means CreateChannelRequest returned but its chat id was unreadable. Retrying
// either would make a SECOND real channel — there is no idempotency key (no
// random_id, nothing keys on the title) and the username pre-check still reports
// the handle free. Everything else — the occupied-handle pre-check,
// channels_too_much, user_restricted, a 503 pool outage, a 422, a dead socket —
// is refused BEFORE CreateChannelRequest runs, so Create must stay armed: those
// are the common failures, and locking them costs the operator the title, the
// about and the username they typed.
const POST_CREATE_CODES = new Set(['flood_wait', 'peer_flood', 'channel_create_failed']);

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
  // Phrased as the operator asked for it ("disable reactions"), so the default —
  // Telegram's own, reactions on — is the unchecked box.
  const [reactionsOff, setReactionsOff] = useState(false);
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
  // A create that FAILED with one of POST_CREATE_CODES can have made the channel
  // too, and nothing tells that apart from a create that never happened — so
  // that dialog gets one attempt only.
  const [blocked, setBlocked] = useState(false);
  // Set when a refusal carried the created channel's id: the create SUCCEEDED
  // and only the public-username step failed, so the channel exists as private.
  const [createdId, setCreatedId] = useState<string | null>(null);
  // The up-front gate `_channelsShared.ts` says the probe exists to provide: a
  // DEFINITE "taken" verdict for exactly the handle that is typed now. Nothing
  // else blocks — an absent, debouncing, in-flight, stale or FAILED probe is
  // indistinguishable from "not checked yet", and locking on those would put
  // back the dead end last round removed (the operator could no longer correct
  // the handle). Without this, the hint read «Юзернейм занят» while Create
  // stayed blue, guaranteeing a round-trip that cannot succeed.
  const usernameTaken =
    isPublic &&
    usernameValid &&
    username === debounced &&
    !check.isFetching &&
    check.data?.available === false;
  const canSubmit =
    !busy &&
    !done &&
    !blocked &&
    createdId === null &&
    title.trim().length >= 1 &&
    title.trim().length <= CHANNEL_TITLE_MAX &&
    about.trim().length <= CHANNEL_ABOUT_MAX &&
    (!isPublic || usernameValid) &&
    !usernameTaken;

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
          reactions_enabled: !reactionsOff,
        },
      },
      {
        onSuccess: (result) => {
          void invalidateList();
          onCreated(result.channel_id ?? null);
        },
        onError: (err) => {
          // The channel may exist as private even though the request failed
          // (occupied username after a successful create), so the list refreshes
          // either way.
          void invalidateList();
          const channelId = errorChannelId(err);
          // An id in the envelope's fields means the create itself SUCCEEDED and
          // one of the post-create steps failed — the public-username assignment
          // or turning reactions off. Create must not re-arm (a second click
          // makes a second real channel), but the hand-off is NOT automatic:
          // unmounting here would take the reason with it, and for the username
          // the editor cannot even fix it — EditChannel carries no username and
          // UpdateUsernameRequest exists nowhere outside _create_channel, so an
          // operator dropped straight into it reads "private" with no idea why.
          // (Reactions ARE fixable there, hence the button below offers the
          // editor rather than doing nothing.) The reason stays on screen.
          if (channelId !== null) {
            setCreatedId(channelId);
            return;
          }
          if (POST_CREATE_CODES.has(envelopeMessage(err) ?? '')) setBlocked(true);
        },
      },
    );
  };

  // Username hint line: format error → debounce/probe spinner → probe failure →
  // verdict.
  let usernameHint: { text: string; tone: 'muted' | 'ok' | 'error' } | null = null;
  if (isPublic) {
    if (username !== '' && !usernameValid) {
      usernameHint = { text: t('accounts.channel.errUsername'), tone: 'error' };
    } else if (usernameValid && (username !== debounced || check.isFetching)) {
      usernameHint = { text: t('accounts.channel.usernameChecking'), tone: 'muted' };
    } else if (usernameValid && check.isError) {
      // A failed probe used to render NOTHING, which looks exactly like "not
      // checked yet". Muted, not error: Create stays armed, so an alarming
      // colour next to a live button would just repeat the contradiction above.
      usernameHint = { text: t('accounts.channel.usernameCheckFailed'), tone: 'muted' };
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
      ? 'text-success'
      : usernameHint?.tone === 'error'
        ? 'text-danger'
        : 'text-ink-subtle';

  return (
    // Escape / backdrop-click route through Modal's onClose — locked while the
    // create is in flight (unmounting mid-flight drops the onSuccess and loses
    // both the list refresh and the editor hand-off).
    <Modal
      onClose={busy ? () => undefined : onClose}
      backdrop={0.45}
      className="w-[460px]"
      label={t('accounts.channel.createTitle')}
    >
      <div className="tb-scroll max-h-[88dvh] overflow-y-auto px-2xl py-2xl">
        <div className="mb-lg flex items-center justify-between">
          <span className="text-title font-bold">{t('accounts.channel.createTitle')}</span>
          <IconButton
            size="md"
            onClick={onClose}
            disabled={busy}
            aria-label={t('accounts.channel.close')}
            className="text-title"
          >
            ×
          </IconButton>
        </div>

        <label className="mb-lg block">
          <span className={LABEL}>{t('accounts.channel.titleLabel')}</span>
          <Input
            value={title}
            maxLength={CHANNEL_TITLE_MAX}
            onChange={(event) => {
              setTitle(event.target.value);
            }}
          />
          {title !== '' && title.trim() === '' && (
            <span className="mt-xs block text-tiny text-danger">
              {t('accounts.channel.errTitle')}
            </span>
          )}
        </label>

        <label className="mb-lg block">
          <span className={LABEL}>{t('accounts.channel.aboutLabel')}</span>
          <Textarea
            className="resize-none [font-family:inherit]"
            rows={3}
            value={about}
            maxLength={CHANNEL_ABOUT_MAX}
            onChange={(event) => {
              setAbout(event.target.value);
            }}
          />
        </label>

        <CheckRow
          label={t('accounts.channel.publicToggle')}
          on={isPublic}
          onToggle={() => {
            setIsPublic((value) => !value);
          }}
        />

        <CheckRow
          label={t('accounts.channel.reactionsToggle')}
          on={reactionsOff}
          onToggle={() => {
            setReactionsOff((value) => !value);
          }}
        />

        {isPublic && (
          <label className="mb-lg block">
            <span className={LABEL}>{t('accounts.channel.usernameLabel')}</span>
            <div className="relative flex items-center">
              <span className="absolute left-3 text-lead text-ink-subtle">@</span>
              <Input
                className="pl-3xl"
                value={username}
                onChange={(event) => {
                  setUsername(event.target.value);
                }}
              />
            </div>
            {usernameHint && (
              <span className={`mt-xs block text-tiny ${hintColor}`}>{usernameHint.text}</span>
            )}
          </label>
        )}

        {create.isError && (
          <Notice tone="danger" className="mb-lg">
            {channelErrorText(create.error, t, t('accounts.channel.error'))}
          </Notice>
        )}

        <div className="mt-xl flex justify-end gap-sm">
          <Button onClick={onClose} disabled={busy}>
            {t('accounts.channel.cancel')}
          </Button>
          {/* Once the channel exists (id-bearing refusal) the primary action is
              the hand-off into its editor, not another create. */}
          <Button
            variant="primary"
            onClick={
              createdId === null
                ? submit
                : () => {
                    onCreated(createdId);
                  }
            }
            disabled={createdId === null && !canSubmit}
          >
            {createdId !== null ? (
              t('accounts.channel.edit')
            ) : busy ? (
              <span className="inline-flex items-center gap-sm">
                <span className="tb-spin inline-block h-[14px] w-[14px] rounded-full border-2 border-white/40 border-t-white" />
                {t('accounts.channel.creating')}
              </span>
            ) : (
              t('accounts.channel.createBtn')
            )}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
