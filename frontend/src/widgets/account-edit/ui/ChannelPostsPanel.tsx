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
import { ConfirmModal, toastError } from '@/shared/ui';

import {
  channelErrorText,
  FIELD,
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

  const saveEdit = () => {
    if (editingId === null || editPost.isPending || editText.trim() === '') return;
    editPost.mutate(
      {
        path: { account_id: accountId, channel_id: channelId, post_id: editingId },
        body: { text: editText.trim() },
      },
      {
        onSuccess: () => {
          setEditingId(null);
        },
        onSettled: refresh,
      },
    );
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
    <div className="mt-5 border-t border-[#f0eeeb] pt-4">
      <div className="mb-[10px] text-[13px] font-semibold">{t('accounts.channel.postsTitle')}</div>

      {/* composer */}
      <div className="rounded-[12px] border border-line bg-white p-3">
        <textarea
          rows={3}
          value={text}
          maxLength={textMax}
          placeholder={t('accounts.channel.composerPlaceholder')}
          onChange={(event) => {
            setText(event.target.value);
          }}
          className={`${FIELD} resize-none [font-family:inherit]`}
        />
        {file && (
          <div className="mt-2 flex items-center gap-[10px] rounded-[10px] border border-line bg-[#f8f7f5] px-[10px] py-2">
            {preview ? (
              <img
                src={preview}
                alt={file.name}
                className="h-[38px] w-[38px] rounded-[8px] border border-black/5 object-cover"
              />
            ) : (
              <span className="flex h-[38px] w-[38px] shrink-0 items-center justify-center rounded-[8px] bg-[#eeedea] text-ink-muted">
                <svg
                  width="16"
                  height="16"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.7"
                >
                  <rect x="3" y="4" width="14" height="16" rx="3" />
                  <path d="m17 9 4-2v10l-4-2" />
                </svg>
              </span>
            )}
            <span className="min-w-0 flex-1 truncate text-[12px] font-medium">{file.name}</span>
            {!busy && (
              <button
                type="button"
                onClick={() => {
                  setFile(null);
                }}
                aria-label={t('accounts.channel.removeFile')}
                className="inline-flex h-[25px] w-[25px] items-center justify-center rounded-full text-ink-subtle"
              >
                ×
              </button>
            )}
          </div>
        )}
        {publish.isError && (
          <div className="mt-2 rounded-[10px] border border-[#f0c9c5] bg-danger-tint px-3 py-[8px] text-[12px] text-danger">
            {channelErrorText(publish.error, t, t('accounts.channel.error'))}
          </div>
        )}
        <div className="mt-2 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => fileInput.current?.click()}
              disabled={busy}
              aria-label={t('accounts.channel.attach')}
              className="inline-flex h-[30px] w-[30px] items-center justify-center rounded-full border border-line-input bg-white text-ink-muted disabled:opacity-50"
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
            </button>
            <span className="text-[11px] text-ink-subtle">
              {t('accounts.channel.charCount', { n: text.length, max: textMax })}
            </span>
          </div>
          <button
            type="button"
            onClick={doPublish}
            disabled={!canPublish}
            className="rounded-full bg-primary px-4 py-[7px] text-[12.5px] font-medium text-white disabled:opacity-50"
          >
            {busy ? (
              <span className="inline-flex items-center gap-[6px]">
                <span className="tb-spin inline-block h-[13px] w-[13px] rounded-full border-2 border-white/40 border-t-white" />
                {t('accounts.channel.publishing')}
              </span>
            ) : (
              t('accounts.channel.publish')
            )}
          </button>
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
          className="flex justify-center py-5"
        >
          <span className="tb-spin inline-block h-5 w-5 rounded-full border-2 border-line-input border-t-primary" />
        </div>
      )}
      {posts.isError && (
        <div className="mt-3 flex items-center justify-between gap-3 rounded-[10px] border border-[#f0c9c5] bg-danger-tint px-3 py-[10px] text-[12.5px] text-danger">
          <span>{channelErrorText(posts.error, t, t('accounts.channel.postsError'))}</span>
          <button
            type="button"
            onClick={() => {
              void posts.refetch();
            }}
            className="shrink-0 rounded-full border border-[#f0c9c5] bg-white px-3 py-[4px] text-[12px] font-medium"
          >
            {t('accounts.channel.retry')}
          </button>
        </div>
      )}
      {posts.isSuccess && items.length === 0 && (
        <div className="mt-3 rounded-[12px] border border-dashed border-line bg-white px-4 py-5 text-center text-[12.5px] text-ink-subtle">
          {t('accounts.channel.postsEmpty')}
        </div>
      )}
      {items.length > 0 && (
        <div className="mt-3 flex flex-col gap-2">
          {items.map((post) => (
            <div key={post.post_id} className="rounded-[12px] border border-line px-[14px] py-3">
              <div className="flex items-center gap-2 text-[11px] text-ink-subtle">
                <span>{formatDate(post.date_unix)}</span>
                {mediaLabel(post.media_kind ?? 'none') && (
                  <span className="rounded-[6px] bg-[#f1efed] px-[6px] py-[1px] font-medium text-ink-muted">
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
                <div className="mt-2">
                  <textarea
                    rows={3}
                    value={editText}
                    maxLength={POST_TEXT_MAX}
                    aria-label={t('accounts.channel.postEdit')}
                    onChange={(event) => {
                      setEditText(event.target.value);
                    }}
                    className={`${FIELD} resize-none [font-family:inherit]`}
                  />
                  {editPost.isError && (
                    <div className="mt-2 rounded-[10px] border border-[#f0c9c5] bg-danger-tint px-3 py-[8px] text-[12px] text-danger">
                      {channelErrorText(editPost.error, t, t('accounts.channel.error'))}
                    </div>
                  )}
                  <div className="mt-2 flex justify-end gap-2">
                    <button
                      type="button"
                      onClick={() => {
                        setEditingId(null);
                      }}
                      disabled={editPost.isPending}
                      className="rounded-full border border-line-input bg-white px-[14px] py-[6px] text-[12px] font-medium disabled:opacity-50"
                    >
                      {t('accounts.channel.postCancel')}
                    </button>
                    <button
                      type="button"
                      onClick={saveEdit}
                      disabled={editPost.isPending || editText.trim() === ''}
                      className="rounded-full bg-primary px-[16px] py-[6px] text-[12px] font-medium text-white disabled:opacity-50"
                    >
                      {t('accounts.channel.postSave')}
                    </button>
                  </div>
                </div>
              ) : (
                post.text !== '' && (
                  <div className="mt-[6px] whitespace-pre-wrap text-[13px] leading-[1.45]">
                    {post.text}
                  </div>
                )
              )}
            </div>
          ))}
        </div>
      )}
      {nextCursor !== null && (
        <button
          type="button"
          onClick={() => {
            void loadMore();
          }}
          disabled={loadingMore}
          className="mt-3 w-full rounded-full border border-line-input bg-white px-4 py-2 text-[12.5px] font-medium text-ink disabled:opacity-50"
        >
          {loadingMore ? (
            <span className="inline-flex items-center gap-[6px]">
              <span className="tb-spin inline-block h-[13px] w-[13px] rounded-full border-2 border-line-input border-t-primary" />
              {t('accounts.channel.loading')}
            </span>
          ) : (
            t('accounts.channel.loadMore')
          )}
        </button>
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
