import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  accountChannelQueryOptions,
  accountChannelsQueryKey,
  setAccountChannelPhotoMutation,
  updateAccountChannelMutation,
} from '@/entities/account';
import {
  Button,
  ConfirmModal,
  IconButton,
  Input,
  Modal,
  Notice,
  Textarea,
  toastError,
} from '@/shared/ui';

import {
  CHANNEL_ABOUT_MAX,
  CHANNEL_TITLE_MAX,
  channelErrorText,
  isUploadablePhoto,
  LABEL,
  PHOTO_MAX_BYTES,
  PHOTO_SUFFIXES,
} from './_channelsShared';
import { CheckRow } from './_CheckRow';
import { ChannelPostsPanel } from './ChannelPostsPanel';

// Channel editor (opened above the profile modal, z=75): title/about edit
// (partial update — only changed fields are sent), avatar upload, and the
// posts panel underneath. Local edits are `null` until touched so the live
// detail keeps showing through and "dirty" is trivially derivable.
export function ChannelEditModal({
  accountId,
  channelId,
  onClose,
}: {
  accountId: string;
  channelId: string;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const detailOpts = accountChannelQueryOptions({
    path: { account_id: accountId, channel_id: channelId },
  });
  const detail = useQuery(detailOpts);
  const update = useMutation(updateAccountChannelMutation());
  const setPhoto = useMutation(setAccountChannelPhotoMutation());
  const photoInput = useRef<HTMLInputElement>(null);
  const [title, setTitle] = useState<string | null>(null);
  const [about, setAbout] = useState<string | null>(null);
  const [reactionsOff, setReactionsOff] = useState<boolean | null>(null);
  const [confirmDiscard, setConfirmDiscard] = useState(false);

  const shownTitle = title ?? detail.data?.title ?? '';
  const shownAbout = about ?? detail.data?.about ?? '';
  // ONE predicate for "the live channel has reactions off", used by both the
  // prefill and the dirty check. Deriving it twice let them disagree: the field
  // is optional in the generated client (the backend defaults it, so OpenAPI
  // marks it not-required), and for an absent value `=== false` said "on" while
  // `!value` said "off" — the box checked itself and Save stayed dead forever.
  const liveReactionsOff = detail.data?.reactions_enabled === false;
  // Same "null until touched" rule as the text fields: the live detail shows
  // through, phrased as the create dialog phrases it (checked = reactions off).
  const shownReactionsOff = reactionsOff ?? liveReactionsOff;
  const titleChanged = detail.data != null && title !== null && title.trim() !== detail.data.title;
  const aboutChanged =
    detail.data != null && about !== null && about.trim() !== (detail.data.about ?? '');
  const reactionsChanged =
    detail.data != null && reactionsOff !== null && reactionsOff !== liveReactionsOff;
  const dirty = titleChanged || aboutChanged || reactionsChanged;
  const busy = update.isPending || setPhoto.isPending;
  // The blank-title guard belongs to the title alone: the title is only sent
  // when it changed, so an about-only edit must stay saveable whatever the title
  // prefill holds — otherwise a detail read that returned a blank title makes
  // Save dead forever, with the "enter a title" hint gated off too.
  const canSave = dirty && !busy && (!titleChanged || shownTitle.trim() !== '');
  // INVARIANT: the detail read never returns a blank title in practice. The
  // backend falls back to one when no chat in the response vector matches the
  // requested id, and that branch is effectively dead — `messages.chatFull`
  // always carries the requested channel, and the id spaces line up because the
  // gateway resolves `PeerChannel(channel_id)`, i.e. the UNMARKED id, which is
  // exactly what `Channel.id` holds. Had a `-100…`-prefixed id ever reached it,
  // no chat would match on EVERY read, not just an odd one: the header would
  // render empty (`??` does not catch '') and the badge below would confidently
  // announce "Private" for a public channel. So a blank title means "nothing
  // known about this channel" and the identity line says nothing at all.
  const detailBlank = detail.isSuccess && detail.data.title === '';

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: detailOpts.queryKey });
    void queryClient.invalidateQueries({
      queryKey: accountChannelsQueryKey({ path: { account_id: accountId } }),
    });
  };

  const save = () => {
    if (!canSave) return;
    update.mutate(
      {
        path: { account_id: accountId, channel_id: channelId },
        // Partial update: unchanged fields are omitted (backend treats absent
        // as "leave as is").
        body: {
          ...(titleChanged ? { title: shownTitle.trim() } : {}),
          ...(aboutChanged ? { about: shownAbout.trim() } : {}),
          ...(reactionsChanged ? { reactions_enabled: !shownReactionsOff } : {}),
        },
      },
      {
        onSuccess: () => {
          // Back to "showing the live detail" — the invalidated refetch
          // carries the just-saved values, and the form is no longer dirty.
          setTitle(null);
          setAbout(null);
          setReactionsOff(null);
        },
        onSettled: invalidate,
      },
    );
  };

  const onPhotoPicked = (event: React.ChangeEvent<HTMLInputElement>) => {
    // Materialize BEFORE clearing — event.target.files is a live FileList.
    const file = event.target.files?.[0] ?? null;
    event.target.value = '';
    if (!file) return;
    // Mirror of the backend avatar gate (suffix allowlist + byte cap) so a bad
    // file is rejected up front with a translated toast.
    if (!isUploadablePhoto(file)) {
      toastError(
        t('accounts.channel.photoRejected', { name: file.name, mb: PHOTO_MAX_BYTES / 1_000_000 }),
      );
      return;
    }
    setPhoto.mutate(
      { path: { account_id: accountId, channel_id: channelId }, body: { file } },
      // Settled, not success: even a failed upload may have changed the
      // channel's photo state on Telegram — re-pull either way.
      { onSettled: invalidate },
    );
  };

  // Escape / backdrop / × ask before discarding unsaved edits; all exits are
  // locked while a write is in flight (unmounting drops the invalidation).
  const requestClose = () => {
    if (busy) return;
    if (dirty) setConfirmDiscard(true);
    else onClose();
  };

  return (
    <>
      <Modal
        onClose={requestClose}
        backdrop={0.45}
        className="w-panel"
        // A fixed name, unlike the visible heading below it: this dialog opens
        // while the detail is still loading, and an ARIA name that changes after
        // the announcement is never re-announced — so the operator would only ever
        // hear "Загрузка…". `??` did not guard '' either, and a blank channel title
        // is a real (if rare) read result, which left the dialog nameless.
        label={t('accounts.channel.dialog')}
      >
        <div className="tb-scroll max-h-dialog overflow-y-auto px-2xl py-2xl">
          <div className="mb-lg flex items-center justify-between gap-md">
            <div className="min-w-0">
              {/* A heading, not a div: the dialog's own name is fixed (see above), so
                  this is the only place the channel's title is exposed, and heading
                  navigation is how a screen-reader user reaches it. */}
              <h2 className="truncate type-dialog-title">
                {detail.data?.title ?? t('accounts.channel.loading')}
              </h2>
              {!detailBlank && (
                <div className="truncate type-prose">
                  {detail.data?.username != null
                    ? `@${detail.data.username}`
                    : t('accounts.channel.privateBadge')}
                  {detail.data?.participants_count != null &&
                    ` · ${t('accounts.channel.participants', { n: detail.data.participants_count })}`}
                </div>
              )}
            </div>
            <IconButton
              size="md"
              onClick={requestClose}
              disabled={busy}
              aria-label={t('accounts.channel.close')}
              className="text-title"
            >
              ×
            </IconButton>
          </div>

          {detail.isError && (
            <Notice tone="danger" className="mb-lg flex items-center justify-between gap-md">
              <span>{channelErrorText(detail.error, t, t('accounts.channel.detailError'))}</span>
              <button
                type="button"
                onClick={() => {
                  void detail.refetch();
                }}
                className="shrink-0 rounded-full border border-danger-line bg-white px-md py-xs text-body font-medium"
              >
                {t('accounts.channel.retry')}
              </button>
            </Notice>
          )}

          {detail.isSuccess && (
            <>
              <label className="mb-lg block">
                <span className={LABEL}>{t('accounts.channel.titleLabel')}</span>
                <Input
                  value={shownTitle}
                  maxLength={CHANNEL_TITLE_MAX}
                  onChange={(event) => {
                    setTitle(event.target.value);
                  }}
                />
                {titleChanged && shownTitle.trim() === '' && (
                  <span className="mt-xs block type-caption text-danger">
                    {t('accounts.channel.errTitle')}
                  </span>
                )}
              </label>
              <label className="mb-lg block">
                <span className={LABEL}>{t('accounts.channel.aboutLabel')}</span>
                <Textarea
                  className="resize-none [font-family:inherit]"
                  rows={3}
                  value={shownAbout}
                  maxLength={CHANNEL_ABOUT_MAX}
                  onChange={(event) => {
                    setAbout(event.target.value);
                  }}
                />
              </label>

              <CheckRow
                label={t('accounts.channel.reactionsToggle')}
                on={shownReactionsOff}
                disabled={busy}
                onToggle={() => {
                  setReactionsOff(!shownReactionsOff);
                }}
              />

              {update.isError && (
                <Notice tone="danger" className="mb-lg">
                  {channelErrorText(update.error, t, t('accounts.channel.error'))}
                </Notice>
              )}

              <div className="flex items-center gap-sm">
                <button
                  type="button"
                  onClick={() => photoInput.current?.click()}
                  disabled={busy}
                  className="rounded-full border border-line bg-white px-lg py-sm text-lead font-medium disabled:opacity-60"
                >
                  {setPhoto.isPending ? (
                    <span className="inline-flex items-center gap-sm">
                      <span className="tb-spin inline-block size-spinner rounded-full border-2 border-line border-t-primary" />
                      {t('accounts.channel.avatarUpload')}
                    </span>
                  ) : (
                    t('accounts.channel.avatarUpload')
                  )}
                </button>
                <span className="flex-1" />
                <Button variant="primary" onClick={save} disabled={!canSave}>
                  {update.isPending ? (
                    <span className="inline-flex items-center gap-sm">
                      <span className="tb-spin inline-block size-spinner rounded-full border-2 border-white/40 border-t-white" />
                      {t('accounts.channel.saving')}
                    </span>
                  ) : (
                    t('accounts.channel.save')
                  )}
                </Button>
              </div>
              <input
                ref={photoInput}
                type="file"
                accept={PHOTO_SUFFIXES.join(',')}
                onChange={onPhotoPicked}
                className="hidden"
              />

              <ChannelPostsPanel accountId={accountId} channelId={channelId} />
            </>
          )}
        </div>
      </Modal>
      {confirmDiscard ? (
        <ConfirmModal
          title={t('accounts.channel.discardTitle')}
          body={t('accounts.channel.discardBody')}
          confirmLabel={t('accounts.channel.discardConfirm')}
          cancelLabel={t('accounts.channel.cancel')}
          onClose={() => {
            setConfirmDiscard(false);
          }}
          onConfirm={onClose}
        />
      ) : null}
    </>
  );
}
