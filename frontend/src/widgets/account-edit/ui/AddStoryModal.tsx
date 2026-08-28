import { useMutation } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { postAccountStoryMutation } from '@/entities/account';
import { Button, Icon, IconButton, Input, Modal, SegmentedControl } from '@/shared/ui';

import { envelopeMessage, POST_CAPTION_MAX, type Translate } from './_channelsShared';
import { retryAfterSeconds } from './_profileShared';
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
//
// The same three code tables the global mutation toast walks
// (shared/lib/query-client.ts), story table FIRST so `failed` keeps its
// story-specific wording. The other two carry what a story publish can also be
// refused with and this namespace has no copy for: the rate-limit family
// (flood_wait, peer_flood, slow_mode_wait, premium_wait) and `unavailable`, all
// reachable through raise_for_result. A chain rather than five new keys — two
// copies of one string in two namespaces are two strings that drift. Anything
// unknown still shows as-is.
function errorText(err: unknown, t: Translate, fallback: string): string {
  const message = envelopeMessage(err);
  if (!message) return fallback;
  return t(
    [
      `accounts.addStory.code.${message}`,
      `accounts.profile.code.${message}`,
      `accounts.channel.code.${message}`,
    ],
    { defaultValue: message, s: retryAfterSeconds(err) ?? '?' },
  );
}

// A 9:16 mini-preview of a collage layout: each cell drawn as a rounded rect
// inside a framed portrait canvas. Selected state paints the cells in primary.
function LayoutIcon({ cells, selected }: { cells: readonly CollageCell[]; selected: boolean }) {
  const w = 26;
  const h = 46;
  const gap = 1.4;
  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} aria-hidden="true">
      {/* Tokens, not hexes: the selected frame matches the button's own
          `border-action-primary`, and the cells inherit the button's `text-action-primary`. */}
      <rect
        x="0.5"
        y="0.5"
        width={w - 1}
        height={h - 1}
        rx="4"
        fill="none"
        className={selected ? 'stroke-action-primary' : 'stroke-line-strong'}
      />
      {cells.map(([x, y, cw, ch], i) => (
        <rect
          key={i}
          x={x * w + gap}
          y={y * h + gap}
          width={cw * w - gap * 2}
          height={ch * h - gap * 2}
          rx="1.4"
          className={selected ? 'fill-current' : 'fill-line-strong'}
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
  const fileInput = useRef<HTMLInputElement>(null);
  const post = useMutation(postAccountStoryMutation());
  const busy = post.isPending;
  const done = post.isSuccess;
  const failed = post.isError;

  const count = images.length;
  const isCollage = video === null && count >= MIN_COLLAGE_IMAGES;
  const hasMedia = video !== null || count > 0;

  // Object URLs for the image tiles, derived synchronously with the images so
  // a remove/reorder never renders one frame of previews[index] against the
  // new order (wrong image under the number, briefly-revoked URL). The effect
  // revokes the previous batch after commit (and on unmount) so blobs don't leak.
  const previews = useMemo(() => images.map((file) => URL.createObjectURL(file)), [images]);
  useEffect(
    () => () => {
      previews.forEach((url) => {
        URL.revokeObjectURL(url);
      });
    },
    [previews],
  );

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
  // Tone from the token the upload state MEANS, so the line matches every other
  // failed/done/neutral hint in the app.
  let metaTone = 'text-content-subtle';
  if (failed) {
    metaText = t('accounts.addStory.stError');
    metaTone = 'text-danger';
  } else if (done) {
    metaText = t('accounts.addStory.stDone');
    metaTone = 'text-success-deep';
  } else if (busy) {
    metaText = t('accounts.addStory.stUploading');
  }
  const errorDetail = errorText(post.error, t, t('accounts.addStory.stError'));

  const onPick = (event: React.ChangeEvent<HTMLInputElement>) => {
    // The add control is disabled while busy/done, but this handler sits on the
    // hidden input rather than the button, so it keeps its own guard: the
    // post.reset() below would detach the mutation observer mid-flight.
    if (busy || done) return;
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

  // The success window's auto-close timer, cleared on unmount: fired after the
  // tree is gone it calls an onClose whose owner has moved on.
  const closeTimer = useRef<number | null>(null);
  useEffect(
    () => () => {
      if (closeTimer.current !== null) window.clearTimeout(closeTimer.current);
    },
    [],
  );

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
          closeTimer.current = window.setTimeout(onClose, 900);
        },
      },
    );
  };

  return (
    // Escape and backdrop-click route through Modal's onClose — guard them
    // while a publish is in flight for the same reason Cancel/× are disabled:
    // unmounting mid-flight drops the onSuccess and loses the grid refresh.
    <Modal
      onClose={busy ? () => undefined : onClose}
      className="w-form"
      label={t('accounts.addStory.title')}
    >
      <div className="tb-scroll max-h-dialog overflow-y-auto px-2xl py-2xl">
        <div className="mb-lg flex items-center justify-between">
          <span className="type-dialog-title">{t('accounts.addStory.title')}</span>
          <IconButton
            size="md"
            onClick={onClose}
            // Closing mid-publish unmounts the mutation observer, and RQ v5
            // then drops the mutate-level onSuccess — the story would land on
            // Telegram but the grid would never refresh. Lock the exits.
            disabled={busy}
            aria-label={t('accounts.addStory.close')}
            className="text-title"
          >
            ×
          </IconButton>
        </div>

        <div className="mb-tight type-label">{t('accounts.addStory.audience')}</div>
        <SegmentedControl
          className="mb-lg"
          value={audience}
          ariaLabel={t('accounts.addStory.audience')}
          options={(['contacts', 'closeFriends', 'public'] as const).map((value) => ({
            value,
            label: t(`accounts.addStory.${value}`),
          }))}
          onChange={(value) => {
            setAudience(value);
          }}
        />

        <label className="mb-lg block">
          <span className="mb-tight block type-label">{t('accounts.addStory.caption')}</span>
          <Input
            value={caption}
            onChange={(event) => {
              setCaption(event.target.value);
            }}
            // Mirrors the server's own Form(max_length=1024) on the caption: past
            // it the whole upload is spent to come back a 422 naming a field the
            // operator can no longer see the end of.
            maxLength={POST_CAPTION_MAX}
            placeholder={t('accounts.addStory.captionPlaceholder')}
          />
        </label>

        <button
          type="button"
          onClick={() => {
            setNoForward((value) => !value);
          }}
          className="mb-lg flex w-full items-center gap-md text-left"
        >
          <span
            className={`flex size-glyph shrink-0 items-center justify-center rounded-sm border ${noForward ? 'border-action-primary bg-action-primary' : 'border-line bg-surface-card'}`}
          >
            {noForward && <Icon name="check" size={14} className="stroke-white" />}
          </span>
          <span className="type-dialog-body text-content-secondary">
            {t('accounts.addStory.noForward')}
          </span>
        </button>

        <div className="mb-tight flex items-center justify-between">
          <span className="type-label">{t('accounts.addStory.media')}</span>
          {video === null && count > 0 && (
            <span className="type-caption">
              {t('accounts.addStory.photoCount', { n: count, max: MAX_COLLAGE_IMAGES })}
            </span>
          )}
        </div>

        {/* Add control — hidden once a collage is full (6 photos). A video
            replaces photos and vice-versa (handled in onPick). Locked while a
            publish is in flight or in its success window: onPick calls
            post.reset(), which detaches the observer — that both re-enables
            Publish (a second story on the live account) and kills the
            mutate-level onSuccess, so the grid never refreshes and the modal
            never closes. Same reason the single-video remove is guarded below. */}
        {!(video === null && count >= MAX_COLLAGE_IMAGES) && (
          <button
            type="button"
            onClick={() => fileInput.current?.click()}
            disabled={busy || done}
            className="flex w-full items-center gap-md rounded-lg border border-dashed border-line bg-surface-card px-lg py-lg text-left disabled:opacity-50"
          >
            <div className="flex size-thumbnail shrink-0 items-center justify-center rounded-lg border border-line bg-surface-card text-action-primary">
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
              <div className="truncate type-item-title">
                {hasMedia ? t('accounts.addStory.addMore') : t('accounts.addStory.dropTitle')}
              </div>
              <div className="mt-px type-caption">
                {t('accounts.addStory.collageHint', { max: MAX_COLLAGE_IMAGES })}
              </div>
            </div>
          </button>
        )}
        {video === null && count >= MAX_COLLAGE_IMAGES && (
          <div className="rounded-lg border border-line bg-surface px-lg py-md text-tiny text-content-subtle">
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
          <div className="mt-md flex flex-wrap gap-sm">
            {images.map((image, index) => (
              <div
                key={`${image.name}-${index}`}
                className="tb-fadeup flex w-readout flex-col gap-xs"
              >
                <div
                  // eslint-disable-next-line design-tokens/no-raw-values -- see the note in the rule: the story preview's own portrait box, one component's internal layout
                  className="relative h-[104px] w-readout overflow-hidden rounded-lg border border-line bg-canvas"
                >
                  <img
                    src={previews[index]}
                    alt={image.name}
                    className="h-full w-full object-cover"
                  />
                  <span className="absolute left-[3px] top-[3px] flex h-badge min-w-badge items-center justify-center rounded-full bg-black/55 px-xs text-tiny font-semibold text-on-inverse">
                    {index + 1}
                  </span>
                  <button
                    type="button"
                    onClick={() => {
                      removeImage(index);
                    }}
                    disabled={busy || done}
                    aria-label={t('accounts.addStory.removePhoto', { n: index + 1 })}
                    className="absolute right-[3px] top-[3px] inline-flex size-glyph items-center justify-center rounded-full bg-black/55 text-on-inverse disabled:opacity-40"
                  >
                    <Icon name="close" size={10} />
                  </button>
                </div>
                <div className="flex items-stretch gap-tight">
                  <button
                    type="button"
                    onClick={() => {
                      moveImage(index, index - 1);
                    }}
                    // moveImage also resets the mutation — see the add control.
                    disabled={index === 0 || busy || done}
                    aria-label={t('accounts.addStory.moveLeft', { n: index + 1 })}
                    className="inline-flex h-bar flex-1 items-center justify-center rounded-sm border border-line bg-surface-card text-content-muted transition hover:bg-canvas hover:text-content-primary active:scale-[0.94] disabled:cursor-not-allowed disabled:opacity-35 disabled:hover:bg-surface-card disabled:hover:text-content-muted"
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
                    disabled={index === count - 1 || busy || done}
                    aria-label={t('accounts.addStory.moveRight', { n: index + 1 })}
                    className="inline-flex h-bar flex-1 items-center justify-center rounded-sm border border-line bg-surface-card text-content-muted transition hover:bg-canvas hover:text-content-primary active:scale-[0.94] disabled:cursor-not-allowed disabled:opacity-35 disabled:hover:bg-surface-card disabled:hover:text-content-muted"
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
          <div className="mt-lg">
            <div className="mb-sm type-label">{t('accounts.addStory.layout')}</div>
            <div className="flex flex-wrap gap-sm">
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
                    // eslint-disable-next-line design-tokens/no-raw-values -- see the note in the rule: the collage-layout tile's own box, one component's internal layout
                    className={`flex h-[62px] w-[46px] items-center justify-center rounded-md border text-action-primary transition ${selected ? 'border-action-primary bg-info-tint' : 'border-line bg-surface-card'}`}
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
          <div className="mt-md tb-fadeup rounded-lg border border-line bg-surface-card px-md py-md">
            <div className="flex items-center gap-md">
              <div className="flex size-tile shrink-0 items-center justify-center rounded-md bg-canvas text-content-muted">
                <Icon name="video" size={16} />
              </div>
              <div className="min-w-0 flex-1">
                <div className="truncate type-item-title">{video.name}</div>
                <div className={`mt-px text-tiny ${metaTone}`}>{metaText}</div>
              </div>
              {!busy && !done && (
                <button
                  type="button"
                  onClick={() => {
                    setVideo(null);
                    post.reset();
                  }}
                  aria-label={t('accounts.addStory.removeFile')}
                  className="inline-flex size-chip items-center justify-center rounded-full text-content-subtle"
                >
                  <Icon name="close" size={14} />
                </button>
              )}
            </div>
          </div>
        )}

        {/* Per-publish status: uploading spinner + bar → success check + full
            bar → error icon (hover = reason) + retry. Shared by both modes. */}
        {hasMedia && (busy || done || failed) && (
          <div className="mt-md tb-fadeup flex items-center gap-md rounded-lg border border-line bg-surface-card px-md py-md">
            <div className="min-w-0 flex-1">
              <div className={`type-caption font-medium ${metaTone}`}>{metaText}</div>
              {(busy || done) && (
                <div className="mt-sm h-meter overflow-hidden rounded-full bg-canvas">
                  <div
                    className={`h-full rounded-full ${done ? 'w-full bg-success' : 'tb-upbar bg-action-primary'}`}
                  />
                </div>
              )}
            </div>
            <div className="flex shrink-0 items-center gap-hair">
              {busy && (
                <span className="tb-spin m-tight inline-block size-spinner rounded-full border-2 border-line border-t-action-primary" />
              )}
              {done && (
                <span className="tb-pop m-xs inline-flex text-success-deep">
                  <Icon name="check-circle" size={18} />
                </span>
              )}
              {failed && (
                <>
                  <span className="group relative m-xs inline-flex text-danger">
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
                      className="pointer-events-none absolute right-0 top-[calc(100%+6px)] z-pop hidden w-max max-w-name whitespace-normal rounded-md bg-term px-md py-sm text-left text-tiny font-normal text-on-inverse shadow-pop group-hover:block"
                    >
                      {errorDetail}
                    </span>
                  </span>
                  <button
                    type="button"
                    onClick={publish}
                    aria-label={t('accounts.addStory.retry')}
                    className="inline-flex size-chip items-center justify-center rounded-full text-content-muted"
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

        <div className="mt-xl flex justify-end gap-sm">
          <Button onClick={onClose} disabled={busy}>
            {t('accounts.addStory.cancel')}
          </Button>
          <Button
            variant="primary"
            onClick={publish}
            // `done` keeps the button locked through the 900ms success-close
            // window — isPending is already false there, and a second click
            // would publish the same story to the live account twice.
            disabled={!hasMedia || busy || done}
          >
            {t('accounts.addStory.publish')}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
