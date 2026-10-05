import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  BULK_MIN_LEAD_MS,
  ScheduleModeControl,
  ScheduleTimeField,
  type ScheduleMode,
} from '@/features/schedule-post';
import { Icon, IconButton, Input, SegmentedControl, toastError } from '@/shared/ui';

import {
  isUploadableMusic,
  isUploadablePhoto,
  isUploadablePostMedia,
  MUSIC_MAX_BYTES,
  MUSIC_SUFFIXES,
  PHOTO_MAX_BYTES,
  PHOTO_SUFFIXES,
  VIDEO_MAX_BYTES,
  VIDEO_SUFFIXES,
} from './_channelsShared';
import { DashedAdd, FilePicker } from './_shared';

// The bulk editor's three media tabs. One file rather than three: they are the
// same tab — a picker, a grid of what was picked, one line of consequence — and
// differ only in tile ratio, accepted suffixes and copy.

// A file the backend would refuse is refused HERE, before a batch uploads it
// seventeen times to learn that. The single-account tabs already prefilter by the
// same rules (`isUploadablePhoto` in ProfileModal, `isUploadablePostMedia` in the
// post composer); the bulk tabs went straight to the picker and would have turned
// one bad pick into one 400 per account.
function keepUploadable(
  files: File[],
  ok: (file: File) => boolean,
  formats: string[],
  maxBytes: number,
  t: (key: string, opts?: Record<string, unknown>) => string,
): File[] {
  const kept: File[] = [];
  for (const file of files) {
    if (ok(file)) kept.push(file);
    else
      toastError(
        t('accounts.bulk.fileRejected', {
          name: file.name,
          formats: formats.join(', '),
          mb: maxBytes / 1_000_000,
        }),
      );
  }
  return kept;
}

// Previews come from object URLs, which leak unless revoked. Held in state (not a
// memo) so the revoke runs on the LIST that made them, never on a newer one.
function usePreviews(files: File[]): string[] {
  const [urls, setUrls] = useState<string[]>([]);
  useEffect(() => {
    const next = files.map((file) => URL.createObjectURL(file));
    setUrls(next);
    return () => {
      for (const url of next) URL.revokeObjectURL(url);
    };
  }, [files]);
  return urls;
}

function Picked({
  files,
  urls,
  ratio,
  onRemove,
  removeLabel,
}: {
  files: File[];
  urls: string[];
  ratio: string;
  onRemove: (index: number) => void;
  removeLabel: string;
}) {
  return (
    <>
      {files.map((file, index) => (
        <div key={`${file.name}-${String(index)}`} className="relative">
          <div
            className="rounded-md border border-line bg-canvas bg-cover bg-center"
            style={{ aspectRatio: ratio, backgroundImage: urls[index] && `url(${urls[index]})` }}
          />
          <IconButton
            size="sm"
            shape="circle"
            aria-label={removeLabel}
            onClick={() => {
              onRemove(index);
            }}
            className="absolute right-2 top-2 border-transparent bg-scrim text-on-inverse hover:border-transparent hover:bg-content-primary hover:text-on-inverse"
          >
            <Icon name="close" size={16} />
          </IconButton>
        </div>
      ))}
    </>
  );
}

// Фото: one avatar for the whole batch, or one per account out of a set.
//
// The two modes are not decoration: the same avatar on seventeen accounts is a
// visible pattern, and «раздать по одному» hands each account its own file,
// cycling the set when the batch is longer than it.
export function BulkPhotoTab({
  files,
  spread,
  onFiles,
  onSpread,
}: {
  files: File[];
  spread: boolean;
  onFiles: (files: File[]) => void;
  onSpread: (spread: boolean) => void;
}) {
  const { t } = useTranslation();
  const urls = usePreviews(files);
  return (
    <div className="flex flex-col gap-4">
      <div className="type-body text-content-subtle">{t('accounts.bulk.photoHint')}</div>
      <SegmentedControl
        variant="outline"
        value={spread ? 'each' : 'one'}
        ariaLabel={t('accounts.bulk.photoMode')}
        options={[
          { value: 'one', label: t('accounts.bulk.photoOne') },
          { value: 'each', label: t('accounts.bulk.photoEach') },
        ]}
        onChange={(value) => {
          onSpread(value === 'each');
        }}
      />
      <div className="grid grid-cols-[repeat(auto-fill,minmax(104px,1fr))] gap-3">
        <Picked
          files={files}
          urls={urls}
          ratio="1"
          removeLabel={t('accounts.bulk.removeFile')}
          onRemove={(index) => {
            onFiles(files.filter((_, i) => i !== index));
          }}
        />
        <FilePicker
          accept={PHOTO_SUFFIXES.join(',')}
          multiple={spread}
          onPick={(picked) => {
            const ok = keepUploadable(
              picked,
              isUploadablePhoto,
              PHOTO_SUFFIXES,
              PHOTO_MAX_BYTES,
              t,
            );
            if (ok.length === 0) return;
            onFiles(spread ? [...files, ...ok] : ok.slice(0, 1));
          }}
        >
          {(open) => <DashedAdd ratio="1" label={t('accounts.profile.upload')} onClick={open} />}
        </FilePicker>
      </div>
      <div className="type-small">
        {spread ? t('accounts.bulk.photoEachNote') : t('accounts.bulk.photoOneNote')}
      </div>
    </div>
  );
}

// Сторис: one story published from every selected account.
export function BulkStoriesTab({
  files,
  caption,
  audience,
  onFiles,
  onCaption,
  onAudience,
}: {
  files: File[];
  caption: string;
  audience: 'contacts' | 'close_friends' | 'public';
  onFiles: (files: File[]) => void;
  onCaption: (caption: string) => void;
  onAudience: (audience: 'contacts' | 'close_friends' | 'public') => void;
}) {
  const { t } = useTranslation();
  const urls = usePreviews(files);
  return (
    <div className="flex flex-col gap-4">
      <div className="type-body text-content-subtle">{t('accounts.bulk.storyHint')}</div>
      <div className="grid grid-cols-[repeat(auto-fill,minmax(96px,1fr))] gap-3">
        <Picked
          files={files}
          urls={urls}
          ratio="9 / 16"
          removeLabel={t('accounts.bulk.removeFile')}
          onRemove={() => {
            onFiles([]);
          }}
        />
        {files.length === 0 && (
          <FilePicker
            accept={[...PHOTO_SUFFIXES, ...VIDEO_SUFFIXES].join(',')}
            multiple={false}
            onPick={(picked) => {
              // A story takes either kind, so the cap depends on the file: the
              // video ceiling is ten times the image one.
              const ok = keepUploadable(
                picked,
                isUploadablePostMedia,
                [...PHOTO_SUFFIXES, ...VIDEO_SUFFIXES],
                VIDEO_MAX_BYTES,
                t,
              );
              if (ok.length > 0) onFiles(ok.slice(0, 1));
            }}
          >
            {(open) => (
              <DashedAdd ratio="9 / 16" label={t('accounts.profile.addStory')} onClick={open} />
            )}
          </FilePicker>
        )}
      </div>
      <label className="flex flex-col gap-2">
        <span className="type-body-medium text-content-secondary">
          {t('accounts.addStory.caption')}
        </span>
        <Input
          value={caption}
          placeholder={t('accounts.addStory.captionPlaceholder')}
          onChange={(event) => {
            onCaption(event.target.value);
          }}
        />
      </label>
      <div className="flex flex-col gap-2">
        <span className="type-body-medium text-content-secondary">
          {t('accounts.addStory.audience')}
        </span>
        <SegmentedControl
          variant="outline"
          value={audience}
          ariaLabel={t('accounts.addStory.audience')}
          options={[
            { value: 'contacts', label: t('accounts.addStory.contacts') },
            { value: 'close_friends', label: t('accounts.addStory.closeFriends') },
            { value: 'public', label: t('accounts.addStory.public') },
          ]}
          onChange={(value) => {
            onAudience(value as 'contacts' | 'close_friends' | 'public');
          }}
        />
      </div>
      <div className="type-small">{t('accounts.bulk.storyNote')}</div>
    </div>
  );
}

// Музыка: one track added to every selected account's profile.
export function BulkMusicTab({
  file,
  onFile,
}: {
  file: File | null;
  onFile: (file: File | null) => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex flex-col gap-4">
      <div className="type-body text-content-subtle">{t('accounts.bulk.musicHint')}</div>
      {file ? (
        <div className="flex items-center gap-4 rounded-md border border-line px-4 py-3">
          <span className="flex size-thumbnail shrink-0 items-center justify-center rounded-full bg-action-primary text-on-action">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
              <path d="M8 5v14l11-7z" />
            </svg>
          </span>
          <div className="min-w-0 flex-1 truncate type-h3">{file.name}</div>
          <IconButton
            size="sm"
            shape="circle"
            aria-label={t('accounts.bulk.removeFile')}
            onClick={() => {
              onFile(null);
            }}
            className="shrink-0"
          >
            <Icon name="close" size={16} />
          </IconButton>
        </div>
      ) : (
        <FilePicker
          accept={MUSIC_SUFFIXES.join(',')}
          multiple={false}
          onPick={(picked) => {
            const ok = keepUploadable(
              picked,
              isUploadableMusic,
              MUSIC_SUFFIXES,
              MUSIC_MAX_BYTES,
              t,
            );
            if (ok.length > 0) onFile(ok[0] ?? null);
          }}
        >
          {(open) => (
            <DashedAdd ratio="4 / 1" label={t('accounts.bulk.musicPick')} onClick={open} />
          )}
        </FilePicker>
      )}
      <div className="type-small">{t('accounts.bulk.musicNote')}</div>
    </div>
  );
}

const MAX_SPREAD_MINUTES = 24 * 60;

// Фото и сторис can wait for a time instead of going out on the click. The first
// account gets the base time, each next one `spread` minutes later plus a random
// shift; the per-account moments are drawn when the batch starts.
export function BulkSchedulePanel({
  mode,
  onMode,
  base,
  onBase,
  spread,
  onSpread,
  now,
  tailTooFar,
}: {
  mode: ScheduleMode;
  onMode: (mode: ScheduleMode) => void;
  base: number | null;
  onBase: (ms: number | null) => void;
  spread: number;
  onSpread: (minutes: number) => void;
  now: number;
  tailTooFar: boolean;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex flex-col gap-3 rounded-md border border-line p-3">
      <ScheduleModeControl value={mode} onChange={onMode} />
      {mode === 'later' && (
        <>
          <div className="flex flex-wrap items-end gap-3">
            <ScheduleTimeField
              value={base}
              onChange={onBase}
              now={now}
              minLeadMs={BULK_MIN_LEAD_MS}
              label={t('accounts.schedule.bulkBase')}
            />
            <label className="flex flex-col gap-2">
              <span className="type-body-medium text-content-secondary">
                {t('accounts.schedule.bulkSpread')}
              </span>
              <Input
                type="number"
                size="xs"
                min={0}
                max={MAX_SPREAD_MINUTES}
                value={spread}
                onChange={(event) => {
                  const value = Math.round(Number(event.target.value));
                  if (Number.isFinite(value)) {
                    onSpread(Math.min(MAX_SPREAD_MINUTES, Math.max(0, value)));
                  }
                }}
              />
            </label>
          </div>
          <div className="type-small">{t('accounts.schedule.bulkNote', { n: spread })}</div>
          {tailTooFar && (
            <div role="alert" className="type-small text-danger-deep">
              {t('accounts.schedule.bulkTooFar')}
            </div>
          )}
        </>
      )}
    </div>
  );
}
