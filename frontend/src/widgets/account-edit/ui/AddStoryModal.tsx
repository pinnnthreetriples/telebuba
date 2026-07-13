import { useMutation } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { postAccountStoryMutation } from '@/entities/account';
import { Modal } from '@/shared/ui';

import {
  type CollageCell,
  MAX_COLLAGE_IMAGES,
  MIN_COLLAGE_IMAGES,
  defaultLayoutId,
  isLayoutValidForCount,
  layoutsForCount,
} from './storyCollageLayouts';

// The design's new-story modal: audience segmented control, caption, a
// no-forward checkbox, and a media dropzone. Opened above the profile modal
// (z=75). Publishing posts a real story (postAccountStory) for the account.
//
// Multi-photo "collage": Telegram has no native multi-photo story, so the
// backend stitches 2..6 photos into one composite using a named layout. The UI
// picks the ordered photos + a layout; a single video stays a single-media
// story with no collage/layout.
type Audience = 'contacts' | 'closeFriends' | 'public';

const PRIVACY: Record<Audience, 'contacts' | 'close_friends' | 'public'> = {
  contacts: 'contacts',
  closeFriends: 'close_friends',
  public: 'public',
};

const FIELD =
  'tb-time w-full rounded-[10px] border border-line-input bg-white px-3 py-[9px] text-[13px] outline-none';

function fileSize(
  file: File | null,
  t: (key: string, opts: Record<string, unknown>) => string,
): string {
  if (!file) return '';
  if (file.size >= 1_048_576)
    return t('accounts.addStory.sizeMb', { n: (file.size / 1_048_576).toFixed(1) });
  return t('accounts.addStory.sizeKb', { n: Math.max(1, Math.round(file.size / 1024)) });
}

// Pull the reason out of the /api/v1 error envelope ({error:{code,message}}) the
// failed publish rejects with, so the hover tooltip shows *why* it failed.
// Known locale-neutral failure codes (story_image_invalid / story_video_invalid)
// translate via accounts.addStory.code.*; anything else shows as-is.
function errorText(
  err: unknown,
  t: (key: string, opts?: Record<string, unknown>) => string,
  fallback: string,
): string {
  const message = (err as { error?: { message?: unknown } } | null)?.error?.message;
  if (typeof message !== 'string' || !message.trim()) return fallback;
  return t(`accounts.addStory.code.${message}`, { defaultValue: message });
}

// A 9:16 mini-preview of a collage layout: each cell drawn as a rounded rect
// inside a framed portrait canvas. Selected state paints the cells in primary.
function LayoutIcon({ cells, selected }: { cells: readonly CollageCell[]; selected: boolean }) {
  const w = 26;
  const h = 46;
  const gap = 1.4;
  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} aria-hidden="true">
      <rect
        x="0.5"
        y="0.5"
        width={w - 1}
        height={h - 1}
        rx="4"
        fill="none"
        stroke={selected ? 'var(--tw-prose-primary, #5b8def)' : '#d9d7d2'}
      />
      {cells.map(([x, y, cw, ch], i) => (
        <rect
          key={i}
          x={x * w + gap}
          y={y * h + gap}
          width={cw * w - gap * 2}
          height={ch * h - gap * 2}
          rx="1.4"
          fill={selected ? 'currentColor' : '#c9c7c2'}
        />
      ))}
    </svg>
  );
}

export function AddStoryModal({
  accountId,
  onClose,
  onPosted,
}: {
  accountId: string;
  onClose: () => void;
  onPosted: () => void;
}) {
  const { t } = useTranslation();
  const [audience, setAudience] = useState<Audience>('contacts');
  const [noForward, setNoForward] = useState(false);
  const [caption, setCaption] = useState('');
  // Ordered collage photos (image #1 first) OR a single video — never both.
  const [images, setImages] = useState<File[]>([]);
  const [video, setVideo] = useState<File | null>(null);
  const [collageLayout, setCollageLayout] = useState<string | null>(null);
  const [previews, setPreviews] = useState<string[]>([]);
  const fileInput = useRef<HTMLInputElement>(null);
  const post = useMutation(postAccountStoryMutation());
  const busy = post.isPending;
  const done = post.isSuccess;
  const failed = post.isError;

  const count = images.length;
  const isCollage = video === null && count >= MIN_COLLAGE_IMAGES;
  const hasMedia = video !== null || count > 0;

  // Object URLs for the image tiles; revoke the previous batch on every change
  // (and unmount) so the blobs don't leak.
  useEffect(() => {
    const urls = images.map((file) => URL.createObjectURL(file));
    setPreviews(urls);
    return () => {
      urls.forEach((url) => {
        URL.revokeObjectURL(url);
      });
    };
  }, [images]);

  // Keep the selected layout valid for the current photo count: below the
  // collage floor there is no layout; otherwise snap to the count's default
  // whenever the current pick isn't valid for the new count.
  useEffect(() => {
    if (count < MIN_COLLAGE_IMAGES || video !== null) {
      setCollageLayout(null);
      return;
    }
    setCollageLayout((current) =>
      isLayoutValidForCount(current, count) ? current : defaultLayoutId(count),
    );
  }, [count, video]);

  let metaText = fileSize(video, t);
  let metaColor = '#9a9893';
  if (failed) {
    metaText = t('accounts.addStory.stError');
    metaColor = '#c0473f';
  } else if (done) {
    metaText = t('accounts.addStory.stDone');
    metaColor = '#2e9e64';
  } else if (busy) {
    metaText = t('accounts.addStory.stUploading');
  }
  const errorDetail = errorText(post.error, t, t('accounts.addStory.stError'));

  const seg = (on: boolean): string =>
    `flex-1 rounded-[7px] py-[7px] text-[12.5px] font-medium transition ${on ? 'bg-white text-ink shadow-sm' : 'text-ink-muted'}`;

  const onPick = (event: React.ChangeEvent<HTMLInputElement>) => {
    // Materialize the FileList BEFORE clearing the input — reading files off a
    // live FileList after value='' yields an empty list in real browsers.
    const picked = Array.from(event.target.files ?? []);
    event.target.value = '';
    post.reset();
    if (picked.length === 0) return;
    const videos = picked.filter((file) => file.type.startsWith('video'));
    const photos = picked.filter((file) => !file.type.startsWith('video'));
    // A video is single-media: it wins and clears any staged photos. Otherwise
    // append photos (capped at the collage max) and drop any staged video.
    if (videos.length > 0) {
      setVideo(videos[0] ?? null);
      setImages([]);
      return;
    }
    setVideo(null);
    setImages((prev) => [...prev, ...photos].slice(0, MAX_COLLAGE_IMAGES));
  };

  const moveImage = (from: number, to: number) => {
    if (to < 0 || to >= count) return;
    setImages((prev) => {
      const next = [...prev];
      const [moved] = next.splice(from, 1);
      if (moved) next.splice(to, 0, moved);
      return next;
    });
    post.reset();
  };

  const removeImage = (index: number) => {
    setImages((prev) => prev.filter((_, i) => i !== index));
    post.reset();
  };

  const publish = () => {
    const files = video !== null ? [video] : images;
    if (files.length === 0) return;
    post.mutate(
      {
        path: { account_id: accountId },
        body: {
          files,
          media_kind: video !== null ? 'video' : 'image',
          caption: caption.trim() || null,
          privacy_preset: PRIVACY[audience],
          protect_content: noForward,
          collage_layout: isCollage ? collageLayout : null,
        },
      },
      {
        // Hold the modal open a beat so the success (tb-pop check + full bar)
        // animation plays before the profile refresh + close, per the design.
        onSuccess: () => {
          onPosted();
          window.setTimeout(onClose, 900);
        },
      },
    );
  };

  return (
    <Modal onClose={onClose} z={75} backdrop={0.45} className="w-[460px]">
      <div className="tb-scroll max-h-[88vh] overflow-y-auto px-6 py-[22px]">
        <div className="mb-4 flex items-center justify-between">
          <span className="text-[16px] font-bold">{t('accounts.addStory.title')}</span>
          <button
            type="button"
            onClick={onClose}
            aria-label={t('accounts.addStory.close')}
            className="h-[30px] w-[30px] rounded-full border border-line bg-white text-[16px] text-ink-muted"
          >
            ×
          </button>
        </div>

        <div className="mb-[6px] text-[12px] font-medium text-[#3a3a3a]">
          {t('accounts.addStory.audience')}
        </div>
        <div className="mb-[14px] flex gap-1 rounded-[10px] bg-[#f1efed] p-1">
          {(['contacts', 'closeFriends', 'public'] as const).map((value) => (
            <button
              key={value}
              type="button"
              onClick={() => {
                setAudience(value);
              }}
              className={seg(audience === value)}
            >
              {t(`accounts.addStory.${value}`)}
            </button>
          ))}
        </div>

        <label className="mb-[14px] block">
          <span className="mb-[6px] block text-[12px] font-medium text-[#3a3a3a]">
            {t('accounts.addStory.caption')}
          </span>
          <input
            value={caption}
            onChange={(event) => {
              setCaption(event.target.value);
            }}
            placeholder={t('accounts.addStory.captionPlaceholder')}
            className={FIELD}
          />
        </label>

        <button
          type="button"
          onClick={() => {
            setNoForward((value) => !value);
          }}
          className="mb-4 flex w-full items-center gap-[10px] text-left"
        >
          <span
            className={`flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-[5px] border ${noForward ? 'border-primary bg-primary' : 'border-line-input bg-white'}`}
          >
            {noForward && (
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
          <span className="text-[13px] text-[#3a3a3a]">{t('accounts.addStory.noForward')}</span>
        </button>

        <div className="mb-[6px] flex items-center justify-between">
          <span className="text-[12px] font-medium text-[#3a3a3a]">
            {t('accounts.addStory.media')}
          </span>
          {video === null && count > 0 && (
            <span className="text-[11px] text-ink-subtle">
              {t('accounts.addStory.photoCount', { n: count, max: MAX_COLLAGE_IMAGES })}
            </span>
          )}
        </div>

        {/* Add control — hidden once a collage is full (6 photos). A video
            replaces photos and vice-versa (handled in onPick). */}
        {!(video === null && count >= MAX_COLLAGE_IMAGES) && (
          <button
            type="button"
            onClick={() => fileInput.current?.click()}
            className="flex w-full items-center gap-[11px] rounded-[12px] border border-dashed border-line bg-white px-4 py-[14px] text-left"
          >
            <div className="flex h-[42px] w-[42px] shrink-0 items-center justify-center rounded-[11px] border border-line bg-white text-primary">
              <svg
                width="20"
                height="20"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.7"
              >
                <rect x="3" y="3" width="18" height="18" rx="3" />
                <path d="M3 15l5-5 4 4M14 14l3-3 4 4" />
                <circle cx="9" cy="9" r="1.6" />
              </svg>
            </div>
            <div className="min-w-0 flex-1">
              <div className="truncate text-[12.5px] font-semibold">
                {hasMedia ? t('accounts.addStory.addMore') : t('accounts.addStory.dropTitle')}
              </div>
              <div className="mt-px text-[11px] text-ink-subtle">
                {t('accounts.addStory.collageHint', { max: MAX_COLLAGE_IMAGES })}
              </div>
            </div>
          </button>
        )}
        {video === null && count >= MAX_COLLAGE_IMAGES && (
          <div className="rounded-[12px] border border-line bg-[#f8f7f5] px-4 py-3 text-[11.5px] text-ink-subtle">
            {t('accounts.addStory.maxReached', { max: MAX_COLLAGE_IMAGES })}
          </div>
        )}
        <input
          ref={fileInput}
          type="file"
          accept="image/*,video/*"
          multiple
          className="hidden"
          onChange={onPick}
        />

        {/* Image tiles: ordered previews with reorder (◀ ▶) + remove (×). The
            tile order is the collage cell order sent to the backend. */}
        {video === null && count > 0 && (
          <div className="mt-[10px] flex flex-wrap gap-2">
            {images.map((image, index) => (
              <div
                key={`${image.name}-${index}`}
                className="tb-fadeup flex w-[74px] flex-col gap-[3px]"
              >
                <div className="relative h-[104px] w-[74px] overflow-hidden rounded-[10px] border border-line bg-[#f4f3f0]">
                  <img
                    src={previews[index]}
                    alt={image.name}
                    className="h-full w-full object-cover"
                  />
                  <span className="absolute left-[3px] top-[3px] flex h-[16px] min-w-[16px] items-center justify-center rounded-full bg-black/55 px-[4px] text-[9.5px] font-semibold text-white">
                    {index + 1}
                  </span>
                  <button
                    type="button"
                    onClick={() => {
                      removeImage(index);
                    }}
                    aria-label={t('accounts.addStory.removePhoto', { n: index + 1 })}
                    className="absolute right-[3px] top-[3px] inline-flex h-[18px] w-[18px] items-center justify-center rounded-full bg-black/55 text-white"
                  >
                    <svg
                      width="10"
                      height="10"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2.4"
                    >
                      <path d="M18 6 6 18M6 6l12 12" />
                    </svg>
                  </button>
                </div>
                <div className="flex items-stretch gap-1">
                  <button
                    type="button"
                    onClick={() => {
                      moveImage(index, index - 1);
                    }}
                    disabled={index === 0}
                    aria-label={t('accounts.addStory.moveLeft', { n: index + 1 })}
                    className="inline-flex h-[22px] flex-1 items-center justify-center rounded-[7px] border border-line-input bg-white text-ink-muted transition hover:border-line hover:bg-[#f4f3f0] hover:text-ink active:scale-[0.94] disabled:cursor-not-allowed disabled:opacity-35 disabled:hover:border-line-input disabled:hover:bg-white disabled:hover:text-ink-muted"
                  >
                    <svg
                      width="12"
                      height="12"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2.6"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    >
                      <path d="m15 6-6 6 6 6" />
                    </svg>
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      moveImage(index, index + 1);
                    }}
                    disabled={index === count - 1}
                    aria-label={t('accounts.addStory.moveRight', { n: index + 1 })}
                    className="inline-flex h-[22px] flex-1 items-center justify-center rounded-[7px] border border-line-input bg-white text-ink-muted transition hover:border-line hover:bg-[#f4f3f0] hover:text-ink active:scale-[0.94] disabled:cursor-not-allowed disabled:opacity-35 disabled:hover:border-line-input disabled:hover:bg-white disabled:hover:text-ink-muted"
                  >
                    <svg
                      width="12"
                      height="12"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2.6"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    >
                      <path d="m9 6 6 6-6 6" />
                    </svg>
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Layout picker — only for a 2..6 photo collage. */}
        {isCollage && (
          <div className="mt-[14px]">
            <div className="mb-[7px] text-[12px] font-medium text-[#3a3a3a]">
              {t('accounts.addStory.layout')}
            </div>
            <div className="flex flex-wrap gap-2">
              {layoutsForCount(count).map((layout) => {
                const selected = collageLayout === layout.id;
                return (
                  <button
                    key={layout.id}
                    type="button"
                    onClick={() => {
                      setCollageLayout(layout.id);
                    }}
                    aria-label={t('accounts.addStory.layoutOption', { id: layout.id })}
                    aria-pressed={selected}
                    className={`flex h-[62px] w-[42px] items-center justify-center rounded-[9px] border text-primary transition ${selected ? 'border-primary bg-primary/5' : 'border-line bg-white'}`}
                  >
                    <LayoutIcon cells={layout.cells} selected={selected} />
                  </button>
                );
              })}
            </div>
          </div>
        )}

        {/* Single-video row: filename + size + remove (mirrors the photo path). */}
        {video !== null && (
          <div className="mt-[9px] tb-fadeup rounded-[11px] border border-line bg-white px-[11px] py-[10px]">
            <div className="flex items-center gap-[10px]">
              <div className="flex h-[34px] w-[34px] shrink-0 items-center justify-center rounded-[9px] bg-[#f4f3f0] text-ink-muted">
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
              </div>
              <div className="min-w-0 flex-1">
                <div className="truncate text-[12px] font-semibold">{video.name}</div>
                <div className="mt-px text-[10.5px]" style={{ color: metaColor }}>
                  {metaText}
                </div>
              </div>
              {!busy && !done && (
                <button
                  type="button"
                  onClick={() => {
                    setVideo(null);
                    post.reset();
                  }}
                  aria-label={t('accounts.addStory.removeFile')}
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
              )}
            </div>
          </div>
        )}

        {/* Per-publish status: uploading spinner + bar → success check + full
            bar → error icon (hover = reason) + retry. Shared by both modes. */}
        {hasMedia && (busy || done || failed) && (
          <div className="mt-[10px] tb-fadeup flex items-center gap-[10px] rounded-[11px] border border-line bg-white px-[12px] py-[10px]">
            <div className="min-w-0 flex-1">
              <div className="text-[11.5px] font-medium" style={{ color: metaColor }}>
                {metaText}
              </div>
              {(busy || done) && (
                <div className="mt-2 h-[5px] overflow-hidden rounded-full bg-[#eeedea]">
                  <div
                    className={`h-full rounded-full ${done ? 'w-full bg-[#2e9e64]' : 'tb-upbar bg-primary'}`}
                  />
                </div>
              )}
            </div>
            <div className="flex shrink-0 items-center gap-[2px]">
              {busy && (
                <span className="tb-spin m-[5px] inline-block h-[13px] w-[13px] rounded-full border-2 border-line-input border-t-primary" />
              )}
              {done && (
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
              )}
              {failed && (
                <>
                  <span className="group relative m-[3px] inline-flex text-[#c0473f]">
                    <svg
                      width="17"
                      height="17"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2"
                    >
                      <circle cx="12" cy="12" r="10" />
                      <path d="M12 8v4M12 16h.01" />
                    </svg>
                    <span
                      role="tooltip"
                      className="pointer-events-none absolute right-0 top-[calc(100%+6px)] z-30 hidden w-max max-w-[240px] whitespace-normal rounded-[8px] bg-[#16161a] px-[10px] py-[7px] text-left text-[11px] font-normal leading-[1.5] text-white shadow-[0_6px_20px_rgba(0,0,0,0.18)] group-hover:block"
                    >
                      {errorDetail}
                    </span>
                  </span>
                  <button
                    type="button"
                    onClick={publish}
                    aria-label={t('accounts.addStory.retry')}
                    className="inline-flex h-[25px] w-[25px] items-center justify-center rounded-full text-ink-muted"
                  >
                    <svg
                      width="13"
                      height="13"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="1.9"
                    >
                      <path d="M3 2v6h6" />
                      <path d="M3 8a9 9 0 1 0 2.5-3.5L3 8" />
                    </svg>
                  </button>
                </>
              )}
            </div>
          </div>
        )}

        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-full border border-line-input bg-white px-[18px] py-[9px] text-[13px] font-medium text-ink"
          >
            {t('accounts.addStory.cancel')}
          </button>
          <button
            type="button"
            onClick={publish}
            disabled={!hasMedia || post.isPending}
            className="rounded-full bg-primary px-5 py-[9px] text-[13px] font-medium text-white disabled:opacity-50"
          >
            {t('accounts.addStory.publish')}
          </button>
        </div>
      </div>
    </Modal>
  );
}
