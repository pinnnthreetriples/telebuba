import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { ProfileStoryView } from '@/shared/api';
import { Icon, Modal } from '@/shared/ui';

import { ViewerButton } from './_viewerShared';

// Telegram shows a photo story for ~5 s; a video runs its own length.
const STORY_IMAGE_MS = 5000;
const TICK_MS = 50;
// A press shorter than this is a tap (page), longer is a hold (pause).
const HOLD_MS = 200;

// Full-screen story player for the profile modal's stories tab, modelled on
// Telegram's: segmented progress on top, photos advance after 5 s and videos on
// `ended`, closing after the last one. Tapping the left third goes back, the rest
// forward; holding pauses. ←/→ page, Space pauses, Esc closes (Modal stack).
// If the full media can't load, the poster thumbnail plays on the photo timer.
export function StoryViewer({
  stories,
  index,
  onClose,
}: {
  stories: ProfileStoryView[];
  index: number;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [current, setCurrent] = useState(index);
  const [progress, setProgress] = useState(0);
  const [paused, setPaused] = useState(false);
  const [muted, setMuted] = useState(false);
  const [failed, setFailed] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);
  const pressedAt = useRef(0);
  const story = stories[current];
  const isVideo = story?.kind === 'video' && !!story.media_url && !failed;

  const goTo = (target: number) => {
    if (target >= stories.length) {
      onClose();
      return;
    }
    setCurrent(Math.max(0, target));
    setProgress(0);
    setFailed(false);
  };

  // Photo timer (also the fallback for a video whose media failed to load).
  useEffect(() => {
    if (!story || isVideo || paused) return;
    const id = setInterval(() => {
      setProgress((p) => Math.min(1, p + TICK_MS / STORY_IMAGE_MS));
    }, TICK_MS);
    return () => {
      clearInterval(id);
    };
  }, [story, isVideo, paused]);

  useEffect(() => {
    if (progress >= 1) goTo(current + 1);
  });

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    if (paused) video.pause();
    else void video.play().catch(() => undefined);
  }, [paused]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'ArrowLeft') goTo(current - 1);
      if (event.key === 'ArrowRight') goTo(current + 1);
      if (event.key === ' ') {
        event.preventDefault();
        setPaused((p) => !p);
      }
    };
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('keydown', onKey);
    };
  });

  if (!story) return null;
  const src = story.media_url ?? story.thumb_url;
  return (
    <Modal variant="viewer" label={t('accounts.profile.storyViewer')} onClose={onClose}>
      <div
        role="presentation"
        className="absolute inset-0 flex items-center justify-center py-lg"
        onClick={(event) => {
          if (event.target === event.currentTarget) onClose();
        }}
      >
        <div
          role="presentation"
          data-testid="story-stage"
          style={{ aspectRatio: '9 / 16' }}
          className="relative h-full max-w-full cursor-pointer select-none overflow-hidden rounded-lg bg-term"
          onPointerDown={() => {
            pressedAt.current = Date.now();
            setPaused(true);
          }}
          onPointerUp={(event) => {
            setPaused(false);
            if (Date.now() - pressedAt.current >= HOLD_MS) return;
            const rect = event.currentTarget.getBoundingClientRect();
            const back = event.clientX - rect.left < rect.width / 3;
            goTo(current + (back ? -1 : 1));
          }}
        >
          {isVideo ? (
            <video
              key={story.story_id}
              ref={videoRef}
              src={story.media_url ?? undefined}
              poster={story.thumb_url ?? undefined}
              autoPlay
              playsInline
              muted={muted}
              onTimeUpdate={(event) => {
                const { currentTime, duration } = event.currentTarget;
                if (duration > 0) setProgress(Math.min(0.999, currentTime / duration));
              }}
              onEnded={() => {
                goTo(current + 1);
              }}
              onError={() => {
                setFailed(true);
              }}
              className="size-full object-contain"
            />
          ) : (
            src && (
              <img
                key={story.story_id}
                src={failed ? (story.thumb_url ?? src) : src}
                alt=""
                draggable={false}
                onError={() => {
                  setFailed(true);
                }}
                className="size-full object-contain"
              />
            )
          )}
          <div className="absolute inset-x-sm top-sm flex gap-xs">
            {stories.map((s, i) => (
              <div
                key={s.story_id}
                className="h-rail flex-1 overflow-hidden rounded-full bg-white/30"
              >
                <div
                  data-testid="story-progress"
                  className="h-full bg-surface-card"
                  style={{ width: `${(i < current ? 1 : i === current ? progress : 0) * 100}%` }}
                />
              </div>
            ))}
          </div>
          {story.kind === 'video' && (
            <ViewerButton
              label={t(muted ? 'accounts.profile.unmute' : 'accounts.profile.mute')}
              onClick={() => {
                setMuted((m) => !m);
              }}
              className="right-sm top-xl"
            >
              <Icon name={muted ? 'volume-off' : 'volume'} size={18} />
            </ViewerButton>
          )}
          {story.caption && (
            <div className="absolute inset-x-0 bottom-0 bg-black/55 px-md py-sm text-body text-on-inverse">
              {story.caption}
            </div>
          )}
        </div>
      </div>
      <ViewerButton
        label={t('accounts.profile.closeViewer')}
        onClick={onClose}
        className="right-lg top-lg"
      >
        <Icon name="close" size={20} />
      </ViewerButton>
      {current > 0 && (
        <ViewerButton
          label={t('accounts.profile.viewerPrev')}
          onClick={() => {
            goTo(current - 1);
          }}
          className="left-lg top-1/2 -translate-y-1/2"
        >
          <Icon name="chevron-left" size={20} />
        </ViewerButton>
      )}
      {current < stories.length - 1 && (
        <ViewerButton
          label={t('accounts.profile.viewerNext')}
          onClick={() => {
            goTo(current + 1);
          }}
          className="right-lg top-1/2 -translate-y-1/2"
        >
          <Icon name="chevron-right" size={20} />
        </ViewerButton>
      )}
    </Modal>
  );
}
