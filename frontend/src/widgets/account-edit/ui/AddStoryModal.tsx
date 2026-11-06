import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  accountScheduledPostsQueryKey,
  postAccountStoryMutation,
  scheduleAccountStoryMutation,
  uploadScheduledMediaMutation,
} from '@/entities/account';
import {
  ScheduleModeControl,
  ScheduleTimeField,
  clampToLead,
  defaultRunAt,
  newBatchId,
  runAtProblem,
  toIso,
  useNow,
  type ScheduleMode,
} from '@/features/schedule-post';
import {
  StoryComposer,
  createDefaultDesign,
  designIsValid,
  toWireDesign,
} from '@/features/story-composer';
import { Button, CloseButton, Modal, toastError } from '@/shared/ui';

import {
  envelopeMessage,
  isUploadablePostMedia,
  PHOTO_SUFFIXES,
  VIDEO_MAX_BYTES,
  VIDEO_SUFFIXES,
  type Translate,
} from './_channelsShared';
import { retryAfterSeconds } from './_profileShared';
import { isVideoFile, mergeStoryPick, storyMediaOf, usePreviewUrls } from './_storyShared';
import { StoryMediaPicker } from './StoryMediaPicker';
import { StoryStatusFooter } from './StoryStatusFooter';
import {
  MAX_COLLAGE_IMAGES,
  MIN_COLLAGE_IMAGES,
  defaultLayoutId,
  isLayoutValidForCount,
} from './storyCollageLayouts';

// The design's new-story modal: the story composer (preview with link stickers, caption,
// period, audience, protection) and a media dropzone. Opened above the profile modal
// (z=75). Publishing posts a real story (postAccountStory) for the account.
//
// Multi-photo "collage": Telegram has no native multi-photo story, so the
// backend stitches 2..6 photos into one composite using a named layout. The UI
// picks the ordered photos + a layout; a single video stays a single-media
// story with no collage/layout.

// How long the success window stays open: a story that lost Premium features keeps its
// notice on screen long enough to be read.
const CLOSE_MS = 900;
const CLOSE_NOTICE_MS = 5000;

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

export function AddStoryModal({
  accountId,
  premium = null,
  onClose,
  onPosted,
}: {
  accountId: string;
  // The account's Premium status (null: not known yet).
  premium?: boolean | null;
  onClose: () => void;
  onPosted: () => void;
}) {
  const { t } = useTranslation();
  const [design, setDesign] = useState(createDefaultDesign);
  // Ordered collage photos (image #1 first) OR a single video — never both.
  const [images, setImages] = useState<File[]>([]);
  const [video, setVideo] = useState<File | null>(null);
  const [collageLayout, setCollageLayout] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const post = useMutation(postAccountStoryMutation());
  // "On schedule": the files go to the server's store and a timed post is made
  // from them — one request per file plus one, so the state is kept here rather
  // than read off one mutation.
  const queryClient = useQueryClient();
  const now = useNow();
  const [mode, setMode] = useState<ScheduleMode>('now');
  const [runAt, setRunAt] = useState<number | null>(() => defaultRunAt(Date.now()));
  const [scheduling, setScheduling] = useState(false);
  const [scheduled, setScheduled] = useState(false);
  const [scheduleError, setScheduleError] = useState<unknown>(null);
  const upload = useMutation(uploadScheduledMediaMutation());
  const scheduleStory = useMutation(scheduleAccountStoryMutation());
  // A retry re-sends neither a file already stored nor a post already made: the
  // stored ids are kept per file, and the dialog's key finds a post whose answer
  // was lost.
  const storedIds = useRef(new Map<File, string>());
  const batchId = useRef(newBatchId());
  const later = mode === 'later';
  const busy = post.isPending || scheduling;
  const done = post.isSuccess || scheduled;
  const failed = post.isError || scheduleError !== null;
  const locked = busy || done;
  const resetAll = () => {
    post.reset();
    setScheduleError(null);
  };

  const count = images.length;
  const isCollage = video === null && count >= MIN_COLLAGE_IMAGES;
  const hasMedia = video !== null || count > 0;
  const files = useMemo(() => (video !== null ? [video] : images), [video, images]);
  const previews = usePreviewUrls(files);
  const media = storyMediaOf(files, previews, isCollage ? collageLayout : null);
  // A scheduled story's losses are decided when it goes out: the scheduled list shows them.
  const downgraded = (later ? null : post.data?.downgraded) ?? [];

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
    metaText = t(later ? 'accounts.schedule.storyDone' : 'accounts.addStory.stDone');
    metaTone = 'text-success-deep';
  } else if (busy) {
    metaText = t('accounts.addStory.stUploading');
  }
  const errorDetail = errorText(post.error ?? scheduleError, t, t('accounts.addStory.stError'));

  const onPick = (event: React.ChangeEvent<HTMLInputElement>) => {
    // The add control is disabled while busy/done, but this handler sits on the
    // hidden input rather than the button, so it keeps its own guard: the
    // post.reset() below would detach the mutation observer mid-flight.
    if (locked) return;
    // Materialize the FileList BEFORE clearing the input — reading files off a
    // live FileList after value='' yields an empty list in real browsers.
    const picked = Array.from(event.target.files ?? []);
    event.target.value = '';
    resetAll();
    // A file the backend would refuse is refused here, before the upload spends the
    // request to learn it: the same gate the bulk tab and the channel post composer use.
    const usable = picked.filter((file) => {
      if (isUploadablePostMedia(file)) return true;
      toastError(
        t('accounts.bulk.fileRejected', {
          name: file.name,
          formats: [...PHOTO_SUFFIXES, ...VIDEO_SUFFIXES].join(', '),
          mb: VIDEO_MAX_BYTES / 1_000_000,
        }),
      );
      return false;
    });
    if (usable.length === 0) return;
    // A video is single-media: it wins and clears any staged photos. Otherwise
    // append photos (capped at the collage max) and drop any staged video.
    const next = mergeStoryPick([...images, ...(video ? [video] : [])], usable, MAX_COLLAGE_IMAGES);
    const nextVideo = next.find(isVideoFile) ?? null;
    setVideo(nextVideo);
    setImages(nextVideo ? [] : next);
  };

  const moveImage = (from: number, to: number) => {
    if (to < 0 || to >= count) return;
    setImages((prev) => {
      const next = [...prev];
      const [moved] = next.splice(from, 1);
      if (moved) next.splice(to, 0, moved);
      return next;
    });
    resetAll();
  };

  const removeImage = (index: number) => {
    setImages((prev) => prev.filter((_, i) => i !== index));
    resetAll();
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

  const schedulePublish = async (picked: File[]) => {
    if (runAt === null) return;
    setScheduling(true);
    setScheduleError(null);
    try {
      const mediaIds: string[] = [];
      for (const file of picked) {
        const known = storedIds.current.get(file);
        const mediaId = known ?? (await upload.mutateAsync({ body: { file } })).media_id;
        storedIds.current.set(file, mediaId);
        mediaIds.push(mediaId);
      }
      await scheduleStory.mutateAsync({
        path: { account_id: accountId },
        body: {
          media_ids: mediaIds,
          // Clamped at send time: a long upload can outlive a time picked close in.
          run_at: toIso(clampToLead(runAt, Date.now())),
          batch_id: batchId.current,
          client_key: 'story',
          filename: picked[0]?.name ?? null,
          design: toWireDesign(design),
          collage_layout: isCollage ? collageLayout : null,
        },
      });
      setScheduled(true);
      void queryClient.invalidateQueries({
        queryKey: accountScheduledPostsQueryKey({ path: { account_id: accountId } }),
      });
      // Nothing was published, so the profile grid has nothing to refresh.
      closeTimer.current = window.setTimeout(onClose, CLOSE_MS);
    } catch (error) {
      // A swept file must be uploaded again, not named again.
      if (envelopeMessage(error) === 'scheduled_media_missing') storedIds.current.clear();
      setScheduleError(error);
    } finally {
      setScheduling(false);
    }
  };

  const publish = () => {
    if (files.length === 0) return;
    if (later) {
      void schedulePublish(files);
      return;
    }
    post.mutate(
      {
        path: { account_id: accountId },
        body: {
          files,
          media_kind: video !== null ? 'video' : 'image',
          design: JSON.stringify(toWireDesign(design)),
          collage_layout: isCollage ? collageLayout : null,
        },
      },
      {
        // Hold the modal open a beat so the success (tb-pop check + full bar)
        // animation plays before the profile refresh + close, per the design.
        onSuccess: (result) => {
          onPosted();
          closeTimer.current = window.setTimeout(
            onClose,
            (result.downgraded ?? []).length > 0 ? CLOSE_NOTICE_MS : CLOSE_MS,
          );
        },
      },
    );
  };

  // A story put together but not published. `done` is the success window, which
  // closes by itself and has nothing left to lose.
  const dirty =
    !done &&
    (hasMedia ||
      JSON.stringify(design) !== JSON.stringify(createDefaultDesign()) ||
      mode !== 'now');
  const timeBad = later && runAtProblem(runAt, now) !== null;

  return (
    // Every exit — Escape, backdrop, Cancel/× — is locked while a publish is in
    // flight: unmounting mid-flight drops the onSuccess and loses the grid refresh.
    <Modal
      onClose={onClose}
      dirty={dirty}
      locked={busy}
      size="panel"
      label={t('accounts.addStory.title')}
    >
      {(close) => (
        <div className="tb-scroll max-h-dialog overflow-y-auto px-6 py-6">
          <div className="mb-4 flex items-center justify-between">
            <span className="type-h2">{t('accounts.addStory.title')}</span>
            <CloseButton
              onClick={close}
              // Closing mid-publish unmounts the mutation observer, and RQ v5
              // then drops the mutate-level onSuccess — the story would land on
              // Telegram but the grid would never refresh. Lock the exits.
              disabled={busy}
              aria-label={t('accounts.addStory.close')}
            />
          </div>

          <ScheduleModeControl
            className="mb-3"
            value={mode}
            disabled={locked}
            onChange={(next) => {
              setMode(next);
              resetAll();
            }}
          />
          {later && (
            <div className="mb-4">
              <ScheduleTimeField
                value={runAt}
                onChange={setRunAt}
                now={now}
                label={t('accounts.schedule.storyAt')}
                disabled={locked}
              />
            </div>
          )}

          <div className="mb-4">
            <StoryComposer
              media={media}
              design={design}
              onChange={setDesign}
              premium={premium}
              disabled={locked}
            />
          </div>

          <StoryMediaPicker
            images={images}
            video={video}
            previews={previews}
            locked={locked}
            inputRef={fileInput}
            onPick={onPick}
            onMove={moveImage}
            onRemoveImage={removeImage}
            onRemoveVideo={() => {
              setVideo(null);
              resetAll();
            }}
            layout={collageLayout}
            onLayout={setCollageLayout}
            isCollage={isCollage}
            videoMeta={metaText}
            videoMetaTone={metaTone}
          />

          {hasMedia && (
            <StoryStatusFooter
              busy={busy}
              done={done}
              failed={failed}
              metaText={metaText}
              metaTone={metaTone}
              errorDetail={errorDetail}
              onRetry={publish}
              retryDisabled={timeBad || !designIsValid(design)}
              downgraded={downgraded}
              scheduled={later}
            />
          )}

          <div className="mt-6 flex justify-end gap-2">
            <Button onClick={close} disabled={busy}>
              {t('accounts.addStory.cancel')}
            </Button>
            <Button
              variant="primary"
              onClick={publish}
              // `done` keeps the button locked through the success-close window —
              // isPending is already false there, and a second click would publish
              // the same story to the live account twice.
              disabled={!hasMedia || locked || timeBad || !designIsValid(design)}
            >
              {later ? t('accounts.schedule.storySubmit') : t('accounts.addStory.publish')}
            </Button>
          </div>
        </div>
      )}
    </Modal>
  );
}
