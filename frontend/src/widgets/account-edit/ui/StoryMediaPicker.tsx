import { type ChangeEvent, type RefObject } from 'react';
import { useTranslation } from 'react-i18next';

import { Icon } from '@/shared/ui';

import { StoryCollageLayoutPicker } from './StoryCollageLayoutPicker';
import { MAX_COLLAGE_IMAGES } from './storyCollageLayouts';

const STEP_BUTTON =
  'inline-flex h-bar flex-1 items-center justify-center rounded-sm border border-line bg-surface-card text-content-muted transition hover:bg-canvas hover:text-content-primary active:scale-[0.94] disabled:cursor-not-allowed disabled:opacity-35 disabled:hover:bg-surface-card disabled:hover:text-content-muted';

function Chevron({ d }: { d: string }) {
  return (
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
      <path d={d} />
    </svg>
  );
}

// The story's media: the add control, the ordered photo tiles (reorder and remove), the
// collage layout picker or the single-video row. Every control is locked while a publish
// is in flight or in its success window: a pick or reorder resets the mutation, which
// detaches the observer. That re-enables Publish (a second story on the live account) and
// kills the mutate-level onSuccess, so the grid never refreshes.
export function StoryMediaPicker({
  images,
  video,
  previews,
  locked,
  inputRef,
  onPick,
  onMove,
  onRemoveImage,
  onRemoveVideo,
  layout,
  onLayout,
  isCollage,
  videoMeta,
  videoMetaTone,
}: {
  images: File[];
  video: File | null;
  previews: string[];
  locked: boolean;
  inputRef: RefObject<HTMLInputElement | null>;
  onPick: (event: ChangeEvent<HTMLInputElement>) => void;
  onMove: (from: number, to: number) => void;
  onRemoveImage: (index: number) => void;
  onRemoveVideo: () => void;
  layout: string | null;
  onLayout: (id: string) => void;
  isCollage: boolean;
  videoMeta: string;
  videoMetaTone: string;
}) {
  const { t } = useTranslation();
  const count = images.length;
  const hasMedia = video !== null || count > 0;
  const full = video === null && count >= MAX_COLLAGE_IMAGES;

  return (
    <>
      <div className="mb-2 flex items-center justify-between">
        <span className="type-body-medium text-content-secondary">
          {t('accounts.addStory.media')}
        </span>
        {video === null && count > 0 && (
          <span className="type-small">
            {t('accounts.addStory.photoCount', { n: count, max: MAX_COLLAGE_IMAGES })}
          </span>
        )}
      </div>

      {/* Add control, hidden once a collage is full (6 photos). A video replaces
        photos and vice-versa (handled in onPick). */}
      {!full && (
        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          disabled={locked}
          className="flex w-full items-center gap-3 rounded-md border border-dashed border-line bg-surface-card px-4 py-4 text-left disabled:opacity-50"
        >
          <div className="flex size-thumbnail shrink-0 items-center justify-center rounded-md border border-line bg-surface-card text-action-primary">
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
            <div className="truncate type-body-medium">
              {hasMedia ? t('accounts.addStory.addMore') : t('accounts.addStory.dropTitle')}
            </div>
            <div className="type-small">
              {t('accounts.addStory.collageHint', { max: MAX_COLLAGE_IMAGES })}
            </div>
          </div>
        </button>
      )}
      {full && (
        <div className="rounded-md border border-line bg-surface px-4 py-3 type-small">
          {t('accounts.addStory.maxReached', { max: MAX_COLLAGE_IMAGES })}
        </div>
      )}
      <input
        ref={inputRef}
        type="file"
        accept="image/*,video/*"
        multiple
        className="hidden"
        onChange={onPick}
      />

      {/* Image tiles: ordered previews. The tile order is the collage cell order
        sent to the backend. */}
      {video === null && count > 0 && (
        <div className="mt-3 flex flex-wrap gap-2">
          {images.map((image, index) => (
            <div key={`${image.name}-${index}`} className="tb-fadeup flex w-readout flex-col gap-1">
              <div
                // eslint-disable-next-line design-tokens/no-raw-values -- see the note in the rule: the story preview's own portrait box, one component's internal layout
                className="relative h-[104px] w-readout overflow-hidden rounded-md border border-line bg-canvas"
              >
                <img
                  src={previews[index]}
                  alt={image.name}
                  className="h-full w-full object-cover"
                />
                <span className="absolute left-[3px] top-[3px] flex h-badge min-w-badge items-center justify-center rounded-full bg-black/55 px-1 text-small font-medium text-on-fill">
                  {index + 1}
                </span>
                <button
                  type="button"
                  onClick={() => {
                    onRemoveImage(index);
                  }}
                  disabled={locked}
                  aria-label={t('accounts.addStory.removePhoto', { n: index + 1 })}
                  className="absolute right-[3px] top-[3px] inline-flex size-glyph items-center justify-center rounded-full bg-black/55 text-on-fill disabled:opacity-40"
                >
                  <Icon name="close" size={10} />
                </button>
              </div>
              <div className="flex items-stretch gap-1">
                <button
                  type="button"
                  onClick={() => {
                    onMove(index, index - 1);
                  }}
                  disabled={index === 0 || locked}
                  aria-label={t('accounts.addStory.moveLeft', { n: index + 1 })}
                  className={STEP_BUTTON}
                >
                  <Chevron d="m15 6-6 6 6 6" />
                </button>
                <button
                  type="button"
                  onClick={() => {
                    onMove(index, index + 1);
                  }}
                  disabled={index === count - 1 || locked}
                  aria-label={t('accounts.addStory.moveRight', { n: index + 1 })}
                  className={STEP_BUTTON}
                >
                  <Chevron d="m9 6 6 6-6 6" />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {isCollage && <StoryCollageLayoutPicker count={count} value={layout} onChange={onLayout} />}

      {/* Single-video row: filename + size + remove (mirrors the photo path). */}
      {video !== null && (
        <div className="mt-3 tb-fadeup rounded-md border border-line bg-surface-card px-3 py-3">
          <div className="flex items-center gap-3">
            <div className="flex size-tile shrink-0 items-center justify-center rounded-sm bg-canvas text-content-muted">
              <Icon name="video" size={16} />
            </div>
            <div className="min-w-0 flex-1">
              <div className="truncate type-body-medium">{video.name}</div>
              <div className={`text-small ${videoMetaTone}`}>{videoMeta}</div>
            </div>
            {!locked && (
              <button
                type="button"
                onClick={onRemoveVideo}
                aria-label={t('accounts.addStory.removeFile')}
                className="inline-flex size-chip items-center justify-center rounded-full text-content-subtle"
              >
                <Icon name="close" size={14} />
              </button>
            )}
          </div>
        </div>
      )}
    </>
  );
}
