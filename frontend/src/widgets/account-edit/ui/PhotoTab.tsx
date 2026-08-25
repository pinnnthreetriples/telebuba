import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { ProfilePhotoView } from '@/shared/api';

import { PHOTO_SUFFIXES } from './_channelsShared';
import { tileStyle } from './_profileShared';
import { DashedAdd } from './_shared';

// The profile modal's photo tab: the account's photo history as tiles with
// remove / make-main controls, plus picker + drag-and-drop bulk upload. Upload
// mechanics (prefilter, sequencing, progress) stay in ProfileModal — this tab
// only collects files and raises intents.
export function PhotoTab({
  photos,
  busy,
  uploading,
  onUpload,
  onRemove,
  onMakeMain,
}: {
  photos: ProfilePhotoView[];
  busy: boolean;
  uploading: boolean;
  onUpload: (files: File[]) => void;
  onRemove: (photo: ProfilePhotoView) => void;
  onMakeMain: (photo: ProfilePhotoView) => void;
}) {
  const { t } = useTranslation();
  const photoInput = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);

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
      className={`relative rounded-lg border-[1.5px] border-dashed p-md transition-colors ${dragOver ? 'border-primary' : 'border-transparent'}`}
    >
      {dragOver && (
        <div className="pointer-events-none absolute inset-0 z-raised flex items-center justify-center rounded-lg bg-white/70 text-lead font-medium text-primary">
          {t('accounts.profile.dropPhotos')}
        </div>
      )}
      <div className="mb-md text-body text-ink-subtle">{t('accounts.profile.photoHint')}</div>
      <div className="grid grid-cols-[repeat(auto-fill,minmax(104px,1fr))] gap-md">
        {photos.map((photo) => (
          <div key={photo.photo_id} className="relative">
            <div
              className="rounded-lg border border-black/5"
              style={tileStyle(photo.thumb_url, '1')}
            />
            <button
              type="button"
              aria-label={t('accounts.profile.removePhoto')}
              onClick={() => {
                onRemove(photo);
              }}
              className="absolute right-[6px] top-[6px] size-chip rounded-full bg-scrim text-lead leading-none text-white"
            >
              ×
            </button>
            {photo.is_main ? (
              <span className="mt-tight block w-full py-hair text-tiny font-medium text-primary">
                {t('accounts.profile.mainPhoto')}
              </span>
            ) : (
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  onMakeMain(photo);
                }}
                className="mt-tight block w-full py-hair text-left text-tiny font-medium text-primary hover:underline disabled:opacity-50"
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
      </div>
      <input
        ref={photoInput}
        type="file"
        accept={PHOTO_SUFFIXES.join(',')}
        multiple
        onChange={onPhotosPicked}
        className="hidden"
      />
    </div>
  );
}
