import { useRef } from 'react';
import { useTranslation } from 'react-i18next';

import type { ProfileMusicView } from '@/shared/api';
import { FOCUS_RING, PRESS_FEEDBACK } from '@/shared/design-system';
import { Icon, IconButton } from '@/shared/ui';

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
      <div className="rounded-lg border border-dashed border-line bg-surface-card px-lg py-2xl text-center text-body text-content-subtle">
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
              <span className="flex size-thumbnail shrink-0 items-center justify-center rounded-full bg-action-primary text-on-action">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M8 5v14l11-7z" />
                </svg>
              </span>
              <div className="min-w-0 flex-1">
                <div className="truncate type-card-title">
                  {track.title ?? t('accounts.profile.trackTitle')}
                </div>
                <div className="truncate type-prose">
                  {track.performer ?? t('accounts.profile.trackArtist')}
                </div>
              </div>
              <IconButton
                size="sm"
                shape="circle"
                disabled={!track.file_reference}
                onClick={() => {
                  onRemove(track);
                }}
                aria-label={t('accounts.profile.removeMusic')}
              >
                <Icon name="close" size={16} />
              </IconButton>
            </div>
          ))}
          <IconButton
            size="md"
            aria-label={t('accounts.profile.pickTrack')}
            disabled={busy}
            onClick={() => musicInput.current?.click()}
            className="self-start"
          >
            <Icon name="plus" size={16} aria-hidden="true" />
          </IconButton>
        </div>
      ) : (
        <button
          type="button"
          aria-label={t('accounts.profile.pickTrack')}
          disabled={busy}
          onClick={() => musicInput.current?.click()}
          className={`group relative flex w-full items-center justify-center rounded-lg border border-dashed border-line bg-surface-card px-lg py-2xl text-center text-body text-content-subtle transition duration-state hover:border-info-line hover:bg-action-hover hover:text-info-strong disabled:cursor-not-allowed disabled:opacity-50 ${PRESS_FEEDBACK} ${FOCUS_RING}`}
        >
          <span className="transition-opacity group-hover:opacity-0 group-focus-visible:opacity-0">
            {t('accounts.profile.noMusic')}
          </span>
          <span className="absolute inset-0 flex items-center justify-center gap-sm opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100">
            <Icon name="plus" size={16} aria-hidden="true" />
            {t('accounts.profile.pickTrack')}
          </span>
        </button>
      )}
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
