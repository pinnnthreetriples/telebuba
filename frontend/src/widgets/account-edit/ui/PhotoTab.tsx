import { type ReactNode, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { ProfilePhotoView } from '@/shared/api';
import { Icon, IconButton } from '@/shared/ui';

import { PHOTO_SUFFIXES } from './_channelsShared';
import { HOVER_ONLY, tileStyle } from './_profileShared';
import { DashedAdd } from './_shared';
import { PhotoViewer } from './PhotoViewer';

// The profile modal's photo tab: the account's photo history as tiles with
// remove / make-main controls (click a tile to open the zoomable viewer), plus picker + drag-and-drop bulk upload. Upload
// mechanics (prefilter, sequencing, progress) stay in ProfileModal — this tab
// only collects files and raises intents.
export function PhotoTab({
  photos,
  busy,
  uploading,
  onUpload,
  onRemove,
  onMakeMain,
  onSchedule,
  scheduled,
}: {
  photos: ProfilePhotoView[];
  busy: boolean;
  uploading: boolean;
  onUpload: (files: File[]) => void;
  onRemove: (photo: ProfilePhotoView) => void;
  onMakeMain: (photo: ProfilePhotoView) => void;
  // Opens the timed-upload dialog; the drop zone and the upload tile stay "now".
  onSchedule: () => void;
  // The account's scheduled photos, shown under the grid they will join.
  scheduled: ReactNode;
}) {
  const { t } = useTranslation();
  const photoInput = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);
  const [viewing, setViewing] = useState<number | null>(null);

  const onPhotosPicked = (event: React.ChangeEvent<HTMLInputElement>) => {
    // Materialise the array BEFORE resetting the input — event.target.files is
    // a live FileList, and value='' empties it, so reading it afterwards yields
    // nothing. (jsdom doesn't emulate that clear, which is why tests missed it.)
    const files = Array.from(event.target.files ?? []);
    event.target.value = '';
    onUpload(files);
  };

  return (
    <div
      onDragOver={(event) => {
        event.preventDefault();
        setDragOver(true);
      }}
      onDragLeave={(event) => {
        // Only clear on a real exit — hovering a child tile fires
        // dragleave on the container and would otherwise flicker.
        if (!event.currentTarget.contains(event.relatedTarget as Node)) setDragOver(false);
      }}
      onDrop={(event) => {
        event.preventDefault();
        setDragOver(false);
        // Ignore a second drop while a batch is still uploading.
        if (uploading) return;
        const images = Array.from(event.dataTransfer.files).filter((file) =>
          file.type.startsWith('image/'),
        );
        onUpload(images);
      }}
      className={`relative rounded-md border-[1.5px] border-dashed p-3 transition-colors ${dragOver ? 'border-action-primary' : 'border-transparent'}`}
    >
      {dragOver && (
        <div className="pointer-events-none absolute inset-0 z-raised flex items-center justify-center rounded-md bg-surface-card/70 text-body font-medium text-action-primary">
          {t('accounts.profile.dropPhotos')}
        </div>
      )}
      <div className="mb-3 type-body text-content-subtle">{t('accounts.profile.photoHint')}</div>
      <div className="grid grid-cols-[repeat(auto-fill,minmax(104px,1fr))] gap-3">
        {photos.map((photo, index) => (
          <div key={photo.photo_id} className="group relative">
            <button
              type="button"
              aria-label={t('accounts.profile.openPhoto')}
              disabled={!photo.thumb_url}
              onClick={() => {
                setViewing(index);
              }}
              className="flex w-full cursor-zoom-in items-center justify-center rounded-md border border-black/5 disabled:cursor-default"
              style={tileStyle(photo.thumb_url, '1')}
            >
              {photo.thumb_url && (
                <span
                  className={`flex size-tile items-center justify-center rounded-full bg-black/55 text-on-fill ${HOVER_ONLY}`}
                >
                  <Icon name="zoom-in" size={18} />
                </span>
              )}
            </button>
            <IconButton
              size="sm"
              shape="circle"
              aria-label={t('accounts.profile.removePhoto')}
              onClick={() => {
                onRemove(photo);
              }}
              className={`absolute right-[6px] top-[6px] border-transparent bg-scrim text-on-fill hover:border-transparent hover:bg-content-primary hover:text-on-fill ${HOVER_ONLY}`}
            >
              <Icon name="close" size={16} />
            </IconButton>
            {photo.is_main ? (
              <span className="mt-2 block w-full text-small font-medium text-action-primary">
                {t('accounts.profile.mainPhoto')}
              </span>
            ) : (
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  onMakeMain(photo);
                }}
                className="mt-2 block w-full text-left text-small font-medium text-action-primary hover:underline disabled:opacity-50"
              >
                {t('accounts.profile.makeMain')}
              </button>
            )}
          </div>
        ))}
        <DashedAdd
          ratio="1"
          label={t('accounts.profile.upload')}
          disabled={busy}
          onClick={() => photoInput.current?.click()}
        />
        <DashedAdd
          ratio="1"
          label={t('accounts.schedule.tile')}
          disabled={busy}
          onClick={onSchedule}
        />
      </div>
      {scheduled}
      <input
        ref={photoInput}
        type="file"
        accept={PHOTO_SUFFIXES.join(',')}
        multiple
        onChange={onPhotosPicked}
        className="hidden"
      />
      {viewing !== null && (
        <PhotoViewer
          photos={photos}
          index={viewing}
          onClose={() => {
            setViewing(null);
          }}
        />
      )}
    </div>
  );
}
