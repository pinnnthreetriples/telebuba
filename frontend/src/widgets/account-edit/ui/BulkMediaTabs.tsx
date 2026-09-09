import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Input, SegmentedControl } from '@/shared/ui';

import { PHOTO_SUFFIXES, VIDEO_SUFFIXES } from './_channelsShared';
import { DashedAdd, FilePicker } from './_shared';

// The bulk editor's three media tabs. One file rather than three: they are the
// same tab — a picker, a grid of what was picked, one line of consequence — and
// differ only in tile ratio, accepted suffixes and copy.

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
            className="rounded-lg border border-line bg-canvas bg-cover bg-center"
            style={{ aspectRatio: ratio, backgroundImage: urls[index] && `url(${urls[index]})` }}
          />
          <button
            type="button"
            aria-label={removeLabel}
            onClick={() => {
              onRemove(index);
            }}
            className="absolute right-tight top-tight flex size-chip items-center justify-center rounded-full bg-scrim leading-none text-on-inverse"
          >
            ×
          </button>
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
    <div className="flex flex-col gap-lg">
      <div className="type-prose">{t('accounts.bulk.photoHint')}</div>
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
      <div className="grid grid-cols-[repeat(auto-fill,minmax(104px,1fr))] gap-md">
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
            onFiles(spread ? [...files, ...picked] : picked.slice(0, 1));
          }}
        >
          {(open) => <DashedAdd ratio="1" label={t('accounts.profile.upload')} onClick={open} />}
        </FilePicker>
      </div>
      <div className="type-caption">
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
    <div className="flex flex-col gap-lg">
      <div className="type-prose">{t('accounts.bulk.storyHint')}</div>
      <div className="grid grid-cols-[repeat(auto-fill,minmax(96px,1fr))] gap-md">
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
              onFiles(picked.slice(0, 1));
            }}
          >
            {(open) => (
              <DashedAdd ratio="9 / 16" label={t('accounts.profile.addStory')} onClick={open} />
            )}
          </FilePicker>
        )}
      </div>
      <label className="flex flex-col gap-tight">
        <span className="type-label">{t('accounts.addStory.caption')}</span>
        <Input
          value={caption}
          placeholder={t('accounts.addStory.captionPlaceholder')}
          onChange={(event) => {
            onCaption(event.target.value);
          }}
        />
      </label>
      <div className="flex flex-col gap-tight">
        <span className="type-label">{t('accounts.addStory.audience')}</span>
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
      <div className="type-caption">{t('accounts.bulk.storyNote')}</div>
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
    <div className="flex flex-col gap-lg">
      <div className="type-prose">{t('accounts.bulk.musicHint')}</div>
      {file ? (
        <div className="flex items-center gap-lg rounded-lg border border-line px-lg py-md">
          <span className="flex size-thumbnail shrink-0 items-center justify-center rounded-full bg-action-primary text-on-action">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
              <path d="M8 5v14l11-7z" />
            </svg>
          </span>
          <div className="min-w-0 flex-1 truncate type-card-title">{file.name}</div>
          <button
            type="button"
            aria-label={t('accounts.bulk.removeFile')}
            onClick={() => {
              onFile(null);
            }}
            className="shrink-0 text-title leading-none text-content-muted"
          >
            ×
          </button>
        </div>
      ) : (
        <FilePicker
          accept="audio/*,.mp3,.m4a,.flac,.ogg"
          multiple={false}
          onPick={(picked) => {
            onFile(picked[0] ?? null);
          }}
        >
          {(open) => (
            <DashedAdd ratio="4 / 1" label={t('accounts.bulk.musicPick')} onClick={open} />
          )}
        </FilePicker>
      )}
      <div className="type-caption">{t('accounts.bulk.musicNote')}</div>
    </div>
  );
}
