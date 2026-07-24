import { useRef } from 'react';
import { useTranslation } from 'react-i18next';

import type { ProfileMusicView } from '@/shared/api';

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
      <div className="rounded-[12px] border border-dashed border-line bg-white px-4 py-6 text-center text-[12.5px] text-ink-subtle">
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
        <div className="flex flex-col gap-2">
          {music.map((track) => (
            <div
              key={track.file_id}
              className="flex items-center gap-[13px] rounded-[12px] border border-line px-[14px] py-3"
            >
              <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-primary text-white">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M8 5v14l11-7z" />
                </svg>
              </span>
              <div className="min-w-0 flex-1">
                <div className="truncate text-[13.5px] font-semibold">
                  {track.title ?? t('accounts.profile.trackTitle')}
                </div>
                <div className="truncate text-[12px] text-ink-subtle">
                  {track.performer ?? t('accounts.profile.trackArtist')}
                </div>
              </div>
              <button
                type="button"
                disabled={!track.file_reference}
                onClick={() => {
                  onRemove(track);
                }}
                aria-label={t('accounts.profile.removeMusic')}
                className="h-[30px] w-[30px] rounded-full border border-line bg-white text-[15px] text-ink-subtle disabled:opacity-50"
              >
                ×
              </button>
            </div>
          ))}
        </div>
      ) : (
        <div className="rounded-[12px] border border-dashed border-line bg-white px-4 py-6 text-center text-[12.5px] text-ink-subtle">
          {t('accounts.profile.noMusic')}
        </div>
      )}
      <button
        type="button"
        disabled={busy}
        onClick={() => musicInput.current?.click()}
        className="mt-3 rounded-full border border-line-input bg-white px-4 py-2 text-[13px] font-medium disabled:opacity-60"
      >
        {t('accounts.profile.pickTrack')}
      </button>
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
