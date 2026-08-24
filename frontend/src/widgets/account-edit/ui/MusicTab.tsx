import { useRef } from 'react';
import { useTranslation } from 'react-i18next';

import type { ProfileMusicView } from '@/shared/api';
import { Button, IconButton } from '@/shared/ui';

// The profile modal's music tab: the saved-music list with remove, a picker
// for a new track, and the "unsupported" note for older Telethon builds that
// lack the saved-music TL methods.
export function MusicTab({
  music,
  supported,
  busy,
  onPick,
  onRemove,
}: {
  music: ProfileMusicView[];
  supported: boolean;
  busy: boolean;
  onPick: (file: File) => void;
  onRemove: (track: ProfileMusicView) => void;
}) {
  const { t } = useTranslation();
  const musicInput = useRef<HTMLInputElement>(null);

  if (!supported) {
    return (
      <div className="rounded-lg border border-dashed border-line bg-white px-lg py-2xl text-center text-body text-ink-subtle">
        {t('accounts.profile.musicUnsupported')}
      </div>
    );
  }

  const onMusicPicked = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (file) onPick(file);
  };

  return (
    <div>
      {music.length > 0 ? (
        <div className="flex flex-col gap-sm">
          {music.map((track) => (
            <div
              key={track.file_id}
              className="flex items-center gap-lg rounded-lg border border-line px-lg py-md"
            >
              <span className="flex size-thumbnail shrink-0 items-center justify-center rounded-full bg-primary text-white">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M8 5v14l11-7z" />
                </svg>
              </span>
              <div className="min-w-0 flex-1">
                <div className="truncate text-lead font-semibold">
                  {track.title ?? t('accounts.profile.trackTitle')}
                </div>
                <div className="truncate text-body text-ink-subtle">
                  {track.performer ?? t('accounts.profile.trackArtist')}
                </div>
              </div>
              <IconButton
                size="md"
                disabled={!track.file_reference}
                onClick={() => {
                  onRemove(track);
                }}
                aria-label={t('accounts.profile.removeMusic')}
                className="text-title"
              >
                ×
              </IconButton>
            </div>
          ))}
        </div>
      ) : (
        <div className="rounded-lg border border-dashed border-line bg-white px-lg py-2xl text-center text-body text-ink-subtle">
          {t('accounts.profile.noMusic')}
        </div>
      )}
      <Button
        size="sm"
        className="mt-md"
        loading={busy}
        onClick={() => musicInput.current?.click()}
      >
        {t('accounts.profile.pickTrack')}
      </Button>
      <input
        ref={musicInput}
        type="file"
        accept="audio/*"
        onChange={onMusicPicked}
        className="hidden"
      />
    </div>
  );
}
