import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  accountChannelQueryOptions,
  accountChannelsQueryKey,
  setAccountChannelPhotoMutation,
  updateAccountChannelMutation,
} from '@/entities/account';
import { ConfirmModal, Modal, toastError } from '@/shared/ui';

import {
  CHANNEL_ABOUT_MAX,
  CHANNEL_TITLE_MAX,
  channelErrorText,
  FIELD,
  isUploadablePhoto,
  LABEL,
  PHOTO_MAX_BYTES,
  PHOTO_SUFFIXES,
} from './_channelsShared';
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
  const [confirmDiscard, setConfirmDiscard] = useState(false);

  const shownTitle = title ?? detail.data?.title ?? '';
  const shownAbout = about ?? detail.data?.about ?? '';
  const titleChanged = detail.data != null && title !== null && title.trim() !== detail.data.title;
  const aboutChanged =
    detail.data != null && about !== null && about.trim() !== (detail.data.about ?? '');
  const dirty = titleChanged || aboutChanged;
  const busy = update.isPending || setPhoto.isPending;
  const canSave = dirty && !busy && shownTitle.trim() !== '';

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
        },
      },
      {
        onSuccess: () => {
          // Back to "showing the live detail" — the invalidated refetch
          // carries the just-saved values, and the form is no longer dirty.
          setTitle(null);
          setAbout(null);
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
      <Modal onClose={requestClose} z={75} backdrop={0.45} className="w-[560px]">
        <div className="tb-scroll max-h-[88vh] overflow-y-auto px-6 py-[22px]">
          <div className="mb-4 flex items-center justify-between gap-3">
            <div className="min-w-0">
              <div className="truncate text-[16px] font-bold">
                {detail.data?.title ?? t('accounts.channel.loading')}
              </div>
              <div className="truncate text-[12px] text-ink-subtle">
                {detail.data?.username != null
                  ? `@${detail.data.username}`
                  : t('accounts.channel.privateBadge')}
                {detail.data?.participants_count != null &&
                  ` · ${t('accounts.channel.participants', { n: detail.data.participants_count })}`}
              </div>
            </div>
            <button
              type="button"
              onClick={requestClose}
              disabled={busy}
              aria-label={t('accounts.channel.close')}
              className="h-[30px] w-[30px] shrink-0 rounded-full border border-line bg-white text-[16px] text-ink-muted disabled:opacity-50"
            >
              ×
            </button>
          </div>

          {detail.isError && (
            <div className="mb-4 flex items-center justify-between gap-3 rounded-[10px] border border-[#f0c9c5] bg-danger-tint px-3 py-[10px] text-[12.5px] text-danger">
              <span>{channelErrorText(detail.error, t, t('accounts.channel.detailError'))}</span>
              <button
                type="button"
                onClick={() => {
                  void detail.refetch();
                }}
                className="shrink-0 rounded-full border border-[#f0c9c5] bg-white px-3 py-[4px] text-[12px] font-medium"
              >
                {t('accounts.channel.retry')}
              </button>
            </div>
          )}

          {detail.isSuccess && (
            <>
              <label className="mb-[14px] block">
                <span className={LABEL}>{t('accounts.channel.titleLabel')}</span>
                <input
                  value={shownTitle}
                  maxLength={CHANNEL_TITLE_MAX}
                  onChange={(event) => {
                    setTitle(event.target.value);
                  }}
                  className={FIELD}
                />
                {titleChanged && shownTitle.trim() === '' && (
                  <span className="mt-1 block text-[11.5px] text-danger">
                    {t('accounts.channel.errTitle')}
                  </span>
                )}
              </label>
              <label className="mb-[14px] block">
                <span className={LABEL}>{t('accounts.channel.aboutLabel')}</span>
                <textarea
                  rows={3}
                  value={shownAbout}
                  maxLength={CHANNEL_ABOUT_MAX}
                  onChange={(event) => {
                    setAbout(event.target.value);
                  }}
                  className={`${FIELD} resize-none [font-family:inherit]`}
                />
              </label>

              {update.isError && (
                <div className="mb-[14px] rounded-[10px] border border-[#f0c9c5] bg-danger-tint px-3 py-[10px] text-[12.5px] text-danger">
                  {channelErrorText(update.error, t, t('accounts.channel.error'))}
                </div>
              )}

              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => photoInput.current?.click()}
                  disabled={busy}
                  className="rounded-full border border-line-input bg-white px-4 py-[8px] text-[13px] font-medium disabled:opacity-60"
                >
                  {setPhoto.isPending ? (
                    <span className="inline-flex items-center gap-[6px]">
                      <span className="tb-spin inline-block h-[13px] w-[13px] rounded-full border-2 border-line-input border-t-primary" />
                      {t('accounts.channel.avatarUpload')}
                    </span>
                  ) : (
                    t('accounts.channel.avatarUpload')
                  )}
                </button>
                <span className="flex-1" />
                <button
                  type="button"
                  onClick={save}
                  disabled={!canSave}
                  className="rounded-full bg-primary px-[22px] py-[9px] text-[13px] font-medium text-white disabled:opacity-60"
                >
                  {update.isPending ? (
                    <span className="inline-flex items-center gap-[6px]">
                      <span className="tb-spin inline-block h-[14px] w-[14px] rounded-full border-2 border-white/40 border-t-white" />
                      {t('accounts.channel.saving')}
                    </span>
                  ) : (
                    t('accounts.channel.save')
                  )}
                </button>
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
