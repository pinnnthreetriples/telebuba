import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  accountChannelPostsQueryOptions,
  deleteAccountChannelPostMutation,
  editAccountChannelPostMutation,
  publishAccountChannelPostMutation,
} from '@/entities/account';
import type { ChannelPostView, PageChannelPostView } from '@/shared/api';
import { Button, ConfirmModal, Icon, IconButton, Notice, Textarea, toastError } from '@/shared/ui';

import {
  channelErrorText,
  isUploadablePostMedia,
  PHOTO_MAX_BYTES,
  PHOTO_SUFFIXES,
  POST_CAPTION_MAX,
  POST_TEXT_MAX,
  VIDEO_MAX_BYTES,
  VIDEO_SUFFIXES,
} from './_channelsShared';

// The channel editor's posts block: a composer (text + one optional photo or
// video) above the newest-first post history with cursor-paged "load more",
// inline text edit and confirmed delete.
export function ChannelPostsPanel({
  accountId,
  channelId,
}: {
  accountId: string;
  channelId: string;
}) {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const baseOpts = accountChannelPostsQueryOptions({
    path: { account_id: accountId, channel_id: channelId },
  });
  const posts = useQuery(baseOpts);
  const publish = useMutation(publishAccountChannelPostMutation());
  const editPost = useMutation(editAccountChannelPostMutation());
  const deletePost = useMutation(deleteAccountChannelPostMutation());
  const fileInput = useRef<HTMLInputElement>(null);

  const [text, setText] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editText, setEditText] = useState('');
  const [confirmDelete, setConfirmDelete] = useState<ChannelPostView | null>(null);
  // Pages appended via «load more». Kept separate from the base query so an
  // invalidation refetches page one only; any post mutation clears the tail so
  // a refreshed head and a stale tail can't disagree.
  const [extraPages, setExtraPages] = useState<PageChannelPostView[]>([]);
  const [loadingMore, setLoadingMore] = useState(false);

  const items = [...(posts.data?.items ?? []), ...extraPages.flatMap((page) => page.items)];
  const nextCursor = (extraPages.at(-1) ?? posts.data)?.next_cursor ?? null;

  // Object-URL preview for a staged photo (videos show as a filename row).
  const preview = useMemo(
    () =>
      file && PHOTO_SUFFIXES.some((suffix) => file.name.toLowerCase().endsWith(suffix))
        ? URL.createObjectURL(file)
        : null,
    [file],
  );
  useEffect(
    () => () => {
      if (preview) URL.revokeObjectURL(preview);
    },
    [preview],
  );

  const refresh = () => {
    setExtraPages([]);
    void queryClient.invalidateQueries({ queryKey: baseOpts.queryKey });
  };

  const loadMore = async () => {
    if (nextCursor === null || loadingMore) return;
    setLoadingMore(true);
    try {
      const page = await queryClient.fetchQuery(
        accountChannelPostsQueryOptions({
          path: { account_id: accountId, channel_id: channelId },
          query: { cursor: nextCursor },
        }),
      );
      setExtraPages((prev) => [...prev, page]);
    } catch {
      toastError(t('accounts.channel.postsError'));
    } finally {
      setLoadingMore(false);
    }
  };

  const onPick = (event: React.ChangeEvent<HTMLInputElement>) => {
    // Materialize the file BEFORE clearing the input — event.target.files is a
    // live FileList and value='' empties it in real browsers.
    const picked = event.target.files?.[0] ?? null;
    event.target.value = '';
    if (!picked) return;
    if (!isUploadablePostMedia(picked)) {
      toastError(
        t('accounts.channel.mediaRejected', {
          name: picked.name,
          photoMb: PHOTO_MAX_BYTES / 1_000_000,
          videoMb: VIDEO_MAX_BYTES / 1_000_000,
        }),
      );
      return;
    }
    setFile(picked);
    publish.reset();
  };

  // With media the text becomes the caption (Telegram caps captions at 1024).
  const textMax = file ? POST_CAPTION_MAX : POST_TEXT_MAX;
  const busy = publish.isPending;
  const canPublish = !busy && (text.trim() !== '' || file !== null) && text.length <= textMax;

  const doPublish = () => {
    if (!canPublish) return;
    publish.mutate(
      {
        path: { account_id: accountId, channel_id: channelId },
        body: { text: text.trim(), ...(file ? { file } : {}) },
      },
      {
        onSuccess: () => {
          setText('');
          setFile(null);
          publish.reset();
          refresh();
        },
      },
    );
  };

  // Editing a post that carries media edits its CAPTION, and Telegram caps
  // captions at 1024 — the composer already respects that split, the edit box
  // did not, and the backend has no media-aware branch to catch it.
  const editingMedia = items.find((post) => post.post_id === editingId)?.media_kind ?? 'none';
  const editMax = editingMedia === 'none' ? POST_TEXT_MAX : POST_CAPTION_MAX;
  const canSaveEdit = editText.trim() !== '' && editText.length <= editMax;

  // mutateAsync, not mutate+callbacks: one useMutation is ONE callback slot, and
  // the per-row «Изменить» button below calls editPost.reset(), which drops the
  // observer outright ("there is no way to get it back" upstream). Clicking Edit
  // on another row while this save was in flight therefore lost both the
  // setEditingId(null) and the refresh — Telegram had edited the post and the
  // panel kept showing the old text. A promise per call cannot be taken over.
  const saveEdit = () => {
    if (editingId === null || editPost.isPending || !canSaveEdit) return;
    const savedId = editingId;
    void editPost
      .mutateAsync({
        path: { account_id: accountId, channel_id: channelId, post_id: savedId },
        body: { text: editText.trim() },
      })
      .then(() => {
        // Close the box only if it is still THIS post's: the operator may have
        // moved to another row while the save was in flight.
        setEditingId((current) => (current === savedId ? null : current));
      })
      // finally, not then: a rejected edit may still have changed the channel.
      .finally(refresh)
      // The inline footer above renders editPost.error; nothing else to do here.
      .catch(() => undefined);
  };

  const formatDate = (unix: number): string =>
    new Date(unix * 1000).toLocaleString(i18n.language, {
      day: '2-digit',
      month: '2-digit',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });

  const mediaLabel = (kind: ChannelPostView['media_kind']): string | null => {
    if (kind === 'photo') return t('accounts.channel.mediaPhoto');
    if (kind === 'video') return t('accounts.channel.mediaVideo');
    if (kind === 'other') return t('accounts.channel.mediaOther');
    return null;
  };

  return (
    <div className="mt-xl border-t border-line-row pt-lg">
      <div className="mb-md text-lead font-semibold">{t('accounts.channel.postsTitle')}</div>

      {/* composer */}
      <div className="rounded-lg border border-line bg-white p-md">
        <Textarea
          className="resize-none [font-family:inherit]"
          rows={3}
          value={text}
          maxLength={textMax}
          placeholder={t('accounts.channel.composerPlaceholder')}
          onChange={(event) => {
            setText(event.target.value);
          }}
        />
        {file && (
          <div className="mt-sm flex items-center gap-md rounded-lg border border-line bg-surface px-md py-sm">
            {preview ? (
              <img
                src={preview}
                alt={file.name}
                className="size-thumbnail rounded-md border border-black/5 object-cover"
              />
            ) : (
              <span className="flex size-thumbnail shrink-0 items-center justify-center rounded-md bg-canvas text-ink-muted">
                <Icon name="video" size={16} />
              </span>
            )}
            <span className="min-w-0 flex-1 truncate text-body font-medium">{file.name}</span>
            {!busy && (
              <button
                type="button"
                onClick={() => {
                  setFile(null);
                }}
                aria-label={t('accounts.channel.removeFile')}
                className="inline-flex size-chip items-center justify-center rounded-full text-ink-subtle"
              >
                ×
              </button>
            )}
          </div>
        )}
        {publish.isError && (
          <Notice tone="danger" className="mt-sm py-sm">
            {channelErrorText(publish.error, t, t('accounts.channel.error'))}
          </Notice>
        )}
        <div className="mt-sm flex items-center justify-between">
          <div className="flex items-center gap-sm">
            <IconButton
              size="md"
              onClick={() => fileInput.current?.click()}
              disabled={busy}
              aria-label={t('accounts.channel.attach')}
            >
              <svg
                width="15"
                height="15"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.8"
              >
                <path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48" />
              </svg>
            </IconButton>
            <span className="text-tiny text-ink-subtle">
              {t('accounts.channel.charCount', { n: text.length, max: textMax })}
            </span>
          </div>
          <Button variant="primary" size="sm" onClick={doPublish} disabled={!canPublish}>
            {busy ? (
              <span className="inline-flex items-center gap-sm">
                <span className="tb-spin inline-block size-spinner rounded-full border-2 border-white/40 border-t-white" />
                {t('accounts.channel.publishing')}
              </span>
            ) : (
              t('accounts.channel.publish')
            )}
          </Button>
        </div>
        <input
          ref={fileInput}
          type="file"
          accept={[...PHOTO_SUFFIXES, ...VIDEO_SUFFIXES].join(',')}
          onChange={onPick}
          className="hidden"
        />
      </div>

      {/* posts list */}
      {posts.isPending && (
        <div
          role="status"
          aria-label={t('accounts.channel.loading')}
          className="flex justify-center py-xl"
        >
          <span className="tb-spin inline-block size-chip rounded-full border-2 border-line border-t-primary" />
        </div>
      )}
      {posts.isError && (
        <Notice tone="danger" className="mt-md flex items-center justify-between gap-md">
          <span>{channelErrorText(posts.error, t, t('accounts.channel.postsError'))}</span>
          <button
            type="button"
            onClick={() => {
              void posts.refetch();
            }}
            className="shrink-0 rounded-full border border-danger-line bg-white px-md py-xs text-body font-medium"
          >
            {t('accounts.channel.retry')}
          </button>
        </Notice>
      )}
      {posts.isSuccess && items.length === 0 && (
        <div className="mt-md rounded-lg border border-dashed border-line bg-white px-lg py-xl text-center text-body text-ink-subtle">
          {t('accounts.channel.postsEmpty')}
        </div>
      )}
      {items.length > 0 && (
        <div className="mt-md flex flex-col gap-sm">
          {items.map((post) => (
            <div key={post.post_id} className="rounded-lg border border-line px-lg py-md">
              <div className="flex items-center gap-sm text-tiny text-ink-subtle">
                <span>{formatDate(post.date_unix)}</span>
                {mediaLabel(post.media_kind ?? 'none') && (
                  <span className="rounded-sm bg-canvas px-tight py-px font-medium text-ink-muted">
                    {mediaLabel(post.media_kind ?? 'none')}
                  </span>
                )}
                {post.views != null && (
                  <span>{t('accounts.channel.views', { n: post.views })}</span>
                )}
                <span className="flex-1" />
                <button
                  type="button"
                  onClick={() => {
                    setEditingId(post.post_id);
                    setEditText(post.text ?? '');
                    editPost.reset();
                  }}
                  aria-label={t('accounts.channel.postEdit')}
                  className="font-medium text-primary hover:underline"
                >
                  {t('accounts.channel.postEdit')}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setConfirmDelete(post);
                  }}
                  aria-label={t('accounts.channel.postDelete')}
                  className="font-medium text-danger hover:underline"
                >
                  {t('accounts.channel.postDelete')}
                </button>
              </div>
              {editingId === post.post_id ? (
                <div className="mt-sm">
                  <Textarea
                    className="resize-none [font-family:inherit]"
                    rows={3}
                    value={editText}
                    maxLength={editMax}
                    aria-label={t('accounts.channel.postEdit')}
                    onChange={(event) => {
                      setEditText(event.target.value);
                    }}
                  />
                  {editPost.isError && (
                    <Notice tone="danger" className="mt-sm py-sm">
                      {channelErrorText(editPost.error, t, t('accounts.channel.error'))}
                    </Notice>
                  )}
                  <div className="mt-sm flex items-center justify-end gap-sm">
                    {/* The same readout the composer carries: without it the box
                        just stops accepting input at the media-aware cap with
                        nothing on screen explaining why. */}
                    <span className="mr-auto text-tiny text-ink-subtle">
                      {t('accounts.channel.charCount', { n: editText.length, max: editMax })}
                    </span>
                    <Button
                      size="xs"
                      onClick={() => {
                        setEditingId(null);
                      }}
                      disabled={editPost.isPending}
                    >
                      {t('accounts.channel.postCancel')}
                    </Button>
                    <Button
                      variant="primary"
                      size="xs"
                      onClick={saveEdit}
                      disabled={editPost.isPending || !canSaveEdit}
                    >
                      {t('accounts.channel.postSave')}
                    </Button>
                  </div>
                </div>
              ) : (
                post.text !== '' && (
                  <div className="mt-tight whitespace-pre-wrap text-lead leading-[1.45]">
                    {post.text}
                  </div>
                )
              )}
            </div>
          ))}
        </div>
      )}
      {nextCursor !== null && (
        <Button
          size="sm"
          className="mt-md w-full"
          onClick={() => {
            void loadMore();
          }}
          disabled={loadingMore}
        >
          {loadingMore ? (
            <span className="inline-flex items-center gap-sm">
              <span className="tb-spin inline-block size-spinner rounded-full border-2 border-line border-t-primary" />
              {t('accounts.channel.loading')}
            </span>
          ) : (
            t('accounts.channel.loadMore')
          )}
        </Button>
      )}

      {confirmDelete ? (
        <ConfirmModal
          title={t('accounts.channel.postDeleteTitle')}
          body={t('accounts.channel.postDeleteBody')}
          confirmLabel={t('accounts.channel.postDeleteConfirm')}
          cancelLabel={t('accounts.channel.cancel')}
          onClose={() => {
            setConfirmDelete(null);
          }}
          onConfirm={() =>
            deletePost
              .mutateAsync({
                path: {
                  account_id: accountId,
                  channel_id: channelId,
                  post_id: confirmDelete.post_id,
                },
              })
              // finally, not then: even a failed delete may have changed the
              // channel — re-pull either way; the rejection still propagates
              // so the dialog stays open (global toast reports it).
              .finally(refresh)
          }
        />
      ) : null}
    </div>
  );
}
