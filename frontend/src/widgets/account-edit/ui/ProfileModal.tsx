import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  accountProfileSnapshotQueryOptions,
  addAccountMusicMutation,
  removeAccountMusicMutation,
  removeAccountPhotoMutation,
  removeAccountStoryMutation,
  setAccountPhotoMutation,
  updateAccountProfileMutation,
} from '@/entities/account';
import type {
  AccountRead,
  ProfileMusicView,
  ProfilePhotoView,
  ProfileStoryView,
} from '@/shared/api';
import { ConfirmModal, Modal } from '@/shared/ui';

import { AddStoryModal } from './AddStoryModal';

// The design's profile-edit modal: hero header, a 4-tab segmented header
// (text / photo / stories / music), per-tab bodies, and a save→saved swap
// footer. Every tab is wired to /api/v1: Текст persists the profile, and the
// photo / stories / music tabs render the account's live media (the
// profile-snapshot view) with real upload + remove.
type Tab = 'text' | 'photo' | 'stories' | 'music';

const FIELD =
  'tb-time w-full rounded-[10px] border border-line-input bg-white px-3 py-[9px] text-[13px] outline-none';
const LABEL = 'mb-[6px] block text-[12px] font-medium text-[#3a3a3a]';
// Fallback tile background when a media item carries no thumbnail.
const TILE = 'linear-gradient(135deg,#cfd8ec,#e7dfd2)';

function DashedAdd({
  ratio,
  label,
  onClick,
}: {
  ratio: string;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{ aspectRatio: ratio }}
      className="flex flex-col items-center justify-center gap-[6px] rounded-[12px] border-[1.5px] border-dashed border-[#d2d0cc] bg-white text-[12px] font-medium text-ink-muted"
    >
      <svg
        width="20"
        height="20"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
      >
        <path d="M12 5v14M5 12h14" />
      </svg>
      {label}
    </button>
  );
}

function tileStyle(uri: string | null | undefined, ratio: string): React.CSSProperties {
  if (!uri) return { aspectRatio: ratio, background: TILE };
  return {
    aspectRatio: ratio,
    backgroundImage: `url(${uri})`,
    backgroundSize: 'cover',
    backgroundPosition: 'center',
  };
}

export function ProfileModal({ account, onClose }: { account: AccountRead; onClose: () => void }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const updateProfile = useMutation(updateAccountProfileMutation());
  const setPhoto = useMutation(setAccountPhotoMutation());
  const addMusic = useMutation(addAccountMusicMutation());
  const removeStory = useMutation(removeAccountStoryMutation());
  const removeMusic = useMutation(removeAccountMusicMutation());
  const removePhoto = useMutation(removeAccountPhotoMutation());
  const photoInput = useRef<HTMLInputElement>(null);
  const musicInput = useRef<HTMLInputElement>(null);

  const snapOpts = accountProfileSnapshotQueryOptions({
    path: { account_id: account.account_id },
  });
  const snapshot = useQuery(snapOpts);
  const [refreshing, setRefreshing] = useState(false);
  const photos = snapshot.data?.photos ?? [];
  const stories = snapshot.data?.stories ?? [];
  const music = snapshot.data?.music ?? [];
  const refresh = () => {
    void queryClient.invalidateQueries();
  };
  // "Обновлено {только что | N мин назад}" — from the snapshot query's last fetch.
  const syncMins = snapshot.dataUpdatedAt
    ? Math.floor((Date.now() - snapshot.dataUpdatedAt) / 60000)
    : 0;
  const syncLabel =
    !snapshot.dataUpdatedAt || syncMins < 1
      ? t('accounts.profile.updatedJustNow')
      : t('accounts.profile.updatedMinAgo', { n: syncMins });

  const [tab, setTab] = useState<Tab>('text');
  const [storyOpen, setStoryOpen] = useState(false);
  const [saved, setSaved] = useState(false);
  const [confirmPhoto, setConfirmPhoto] = useState<ProfilePhotoView | null>(null);
  const [confirmStory, setConfirmStory] = useState<ProfileStoryView | null>(null);
  const [confirmMusic, setConfirmMusic] = useState<ProfileMusicView | null>(null);

  const [firstName, setFirstName] = useState(account.first_name ?? '');
  const [lastName, setLastName] = useState(account.last_name ?? '');
  const [username, setUsername] = useState(account.username ?? '');
  const [bio, setBio] = useState(account.bio ?? '');

  // «Обновить»: force a live re-pull (bypasses the read cache), write it into the
  // rendered snapshot, and reseed the header + text fields from the fresh profile.
  const onRefresh = async () => {
    setRefreshing(true);
    try {
      const fresh = await queryClient.fetchQuery(
        accountProfileSnapshotQueryOptions({
          path: { account_id: account.account_id },
          query: { refresh: true },
        }),
      );
      queryClient.setQueryData(snapOpts.queryKey, fresh);
      if (fresh.first_name != null) {
        setFirstName(fresh.first_name);
        setLastName(fresh.last_name ?? '');
        setUsername(fresh.username ?? '');
        setBio(fresh.bio ?? '');
      }
    } finally {
      setRefreshing(false);
    }
  };

  // Header reflects the live snapshot (falls back to the stored account row).
  const liveFirst = snapshot.data?.first_name ?? account.first_name;
  const liveLast = snapshot.data?.last_name ?? account.last_name;
  const liveUser = snapshot.data?.username ?? account.username;
  const initial = (liveFirst ?? account.phone ?? account.account_id).trim().charAt(0).toUpperCase();
  const fullName =
    [liveFirst, liveLast].filter(Boolean).join(' ') || (account.phone ?? account.account_id);

  // Telegram requires a non-empty first name; the Save button gates on it.
  const onSave = () => {
    if (!firstName.trim()) return;
    updateProfile.mutate(
      {
        body: {
          account_id: account.account_id,
          first_name: firstName.trim(),
          last_name: lastName.trim() || null,
          username: username.trim() || null,
          bio: bio.trim() || null,
        },
      },
      {
        onSuccess: () => {
          setSaved(true);
          window.setTimeout(() => {
            setSaved(false);
          }, 1400);
          void queryClient.invalidateQueries();
        },
      },
    );
  };

  const onPhotoPicked = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    setPhoto.mutate({ body: { account_id: account.account_id, file } }, { onSuccess: refresh });
    event.target.value = '';
  };

  const onMusicPicked = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    addMusic.mutate(
      { path: { account_id: account.account_id }, body: { file } },
      { onSuccess: refresh },
    );
    event.target.value = '';
  };

  const tabBtn = (value: Tab): string =>
    `border-b-2 py-[14px] text-[13px] font-medium transition-colors ${tab === value ? 'border-primary text-ink' : 'border-transparent text-ink-muted'}`;

  return (
    <>
      <Modal onClose={onClose} z={70} className="w-[580px]">
        <div className="flex max-h-[88vh] flex-col overflow-hidden">
          {/* header */}
          <div className="flex items-center gap-[14px] border-b border-[#f0eeeb] px-5 py-[18px]">
            <div className="flex h-[52px] w-[52px] shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-[#7c9cff] to-[#a0e0c0] text-[20px] font-semibold text-white">
              {initial}
            </div>
            <div className="min-w-0 flex-1">
              <div className="truncate text-[16px] font-bold">{fullName}</div>
              <div className="truncate text-[12px] text-ink-subtle">
                {liveUser ? `@${liveUser} · ` : ''}
                {account.phone ?? account.account_id}
              </div>
            </div>
            <div className="flex shrink-0 flex-col items-end gap-[5px]">
              <button
                type="button"
                disabled={refreshing}
                onClick={() => {
                  void onRefresh();
                }}
                className="inline-flex items-center gap-[6px] rounded-full border border-line-input bg-white px-3 py-[6px] text-[12.5px] font-medium text-ink transition-colors hover:border-[#bfd6ff] hover:text-primary disabled:opacity-70"
              >
                <span className={`inline-flex ${refreshing ? 'tb-spin' : ''}`}>
                  <svg
                    width="13"
                    height="13"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                  >
                    <path d="M21 2v6h-6M3 12a9 9 0 0 1 15-6.7L21 8M3 22v-6h6M21 12a9 9 0 0 1-15 6.7L3 16" />
                  </svg>
                </span>
                {t('accounts.profile.refresh')}
              </button>
              <span className="text-[11px] text-ink-subtle">{syncLabel}</span>
            </div>
            <button
              type="button"
              onClick={onClose}
              aria-label={t('accounts.profile.close')}
              className="ml-[2px] h-[30px] w-[30px] shrink-0 rounded-full border border-line bg-white text-[16px] text-ink-muted"
            >
              ×
            </button>
          </div>

          {/* tabs */}
          <div className="flex gap-5 border-b border-[#f0eeeb] px-5">
            {(['text', 'photo', 'stories', 'music'] as const).map((value) => (
              <button
                key={value}
                type="button"
                onClick={() => {
                  setTab(value);
                }}
                className={tabBtn(value)}
              >
                {t(`accounts.profile.tab.${value}`)}
              </button>
            ))}
          </div>

          {/* content */}
          <div className="tb-scroll flex-1 overflow-y-auto p-5">
            {tab === 'text' && (
              <div className="flex flex-col gap-[14px]">
                <div className="grid grid-cols-2 gap-3">
                  <label>
                    <span className={LABEL}>{t('accounts.profile.firstName')}</span>
                    <input
                      value={firstName}
                      onChange={(event) => {
                        setFirstName(event.target.value);
                      }}
                      className={FIELD}
                    />
                  </label>
                  <label>
                    <span className={LABEL}>{t('accounts.profile.lastName')}</span>
                    <input
                      value={lastName}
                      onChange={(event) => {
                        setLastName(event.target.value);
                      }}
                      className={FIELD}
                    />
                  </label>
                </div>
                <label>
                  <span className={LABEL}>{t('accounts.profile.username')}</span>
                  <div className="relative flex items-center">
                    <span className="absolute left-3 text-[13px] text-ink-subtle">@</span>
                    <input
                      value={username}
                      onChange={(event) => {
                        setUsername(event.target.value);
                      }}
                      className={`${FIELD} pl-[26px]`}
                    />
                  </div>
                </label>
                <label>
                  <span className={LABEL}>{t('accounts.profile.bio')}</span>
                  <textarea
                    rows={3}
                    value={bio}
                    onChange={(event) => {
                      setBio(event.target.value);
                    }}
                    className={`${FIELD} resize-none [font-family:inherit]`}
                  />
                </label>
              </div>
            )}

            {tab === 'photo' && (
              <div>
                <div className="mb-3 text-[12px] text-ink-subtle">
                  {t('accounts.profile.photoHint')}
                </div>
                <div className="grid grid-cols-[repeat(auto-fill,minmax(104px,1fr))] gap-3">
                  {photos.map((photo, index) => (
                    <div key={photo.photo_id} className="relative">
                      <div
                        className="rounded-[12px] border border-black/5"
                        style={tileStyle(photo.thumb_data_uri, '1')}
                      />
                      <button
                        type="button"
                        aria-label={t('accounts.profile.removePhoto')}
                        onClick={() => {
                          setConfirmPhoto(photo);
                        }}
                        className="absolute right-[6px] top-[6px] h-[22px] w-[22px] rounded-full bg-[rgba(11,11,12,0.55)] text-[13px] leading-none text-white"
                      >
                        ×
                      </button>
                      {/* ponytail: "make main" is a status label, not an action —
                          Telegram has no set-existing-as-main RPC in the gateway. */}
                      <span className="mt-[6px] block w-full py-[2px] text-[11px] font-medium text-primary">
                        {index === 0
                          ? t('accounts.profile.mainPhoto')
                          : t('accounts.profile.makeMain')}
                      </span>
                    </div>
                  ))}
                  <DashedAdd
                    ratio="1"
                    label={t('accounts.profile.upload')}
                    onClick={() => photoInput.current?.click()}
                  />
                </div>
                <input
                  ref={photoInput}
                  type="file"
                  accept="image/*"
                  onChange={onPhotoPicked}
                  className="hidden"
                />
              </div>
            )}

            {tab === 'stories' && (
              <div>
                <div className="mb-3 text-[12px] text-ink-subtle">
                  {t('accounts.profile.storiesHint')}
                </div>
                <div className="grid grid-cols-[repeat(auto-fill,minmax(96px,1fr))] gap-3">
                  {stories.map((story) => (
                    <div key={story.story_id} className="relative">
                      <div
                        className="rounded-[12px] border border-black/5"
                        style={tileStyle(story.thumb_data_uri, '9 / 16')}
                      />
                      <button
                        type="button"
                        aria-label={t('accounts.profile.removeStory')}
                        onClick={() => {
                          setConfirmStory(story);
                        }}
                        className="absolute right-[6px] top-[6px] h-[22px] w-[22px] rounded-full bg-[rgba(11,11,12,0.55)] text-[13px] leading-none text-white"
                      >
                        ×
                      </button>
                      <span className="absolute inset-x-[5px] bottom-[5px] truncate rounded-[6px] bg-[rgba(11,11,12,0.6)] px-[5px] py-[2px] text-center text-[9px] font-medium text-white">
                        {t(`accounts.addStory.${story.privacy_preset}`)}
                      </span>
                    </div>
                  ))}
                  <DashedAdd
                    ratio="9 / 16"
                    label={t('accounts.profile.addStory')}
                    onClick={() => {
                      setStoryOpen(true);
                    }}
                  />
                </div>
              </div>
            )}

            {tab === 'music' && (
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
                            setConfirmMusic(track);
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
                  onClick={() => musicInput.current?.click()}
                  className="mt-3 rounded-full border border-line-input bg-white px-4 py-2 text-[13px] font-medium"
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
            )}
          </div>

          {/* footer */}
          <div className="flex justify-end gap-2 border-t border-[#f0eeeb] px-5 py-[14px]">
            <button
              type="button"
              onClick={onClose}
              className="rounded-full border border-line-input bg-white px-[18px] py-[9px] text-[13px] font-medium text-ink"
            >
              {t('accounts.profile.cancel')}
            </button>
            <button
              type="button"
              onClick={onSave}
              disabled={updateProfile.isPending || !firstName.trim()}
              className={`rounded-full px-[22px] py-[9px] text-[13px] font-medium text-white transition-colors disabled:opacity-60 ${saved ? 'bg-[#2e9e64]' : 'bg-primary'}`}
            >
              {updateProfile.isPending ? (
                <span className="inline-flex items-center gap-[6px]">
                  <span className="tb-spin inline-block h-[14px] w-[14px] rounded-full border-2 border-white/40 border-t-white" />
                  {t('accounts.profile.saving')}
                </span>
              ) : saved ? (
                <span className="inline-flex items-center gap-[6px]">
                  <span className="tb-swapin inline-flex">
                    <svg
                      width="15"
                      height="15"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2.4"
                    >
                      <path d="M20 6 9 17l-5-5" />
                    </svg>
                  </span>
                  <span className="tb-swapin inline-block" style={{ animationDelay: '0.09s' }}>
                    {t('accounts.profile.saved')}
                  </span>
                </span>
              ) : (
                t('accounts.profile.save')
              )}
            </button>
          </div>
        </div>
      </Modal>
      {storyOpen && (
        <AddStoryModal
          accountId={account.account_id}
          onClose={() => {
            setStoryOpen(false);
          }}
          onPosted={refresh}
        />
      )}
      {confirmPhoto ? (
        <ConfirmModal
          title={t('accounts.profile.removePhotoTitle')}
          body={t('accounts.profile.removePhotoBody')}
          confirmLabel={t('accounts.profile.removePhotoConfirm')}
          cancelLabel={t('accounts.profile.cancel')}
          onClose={() => {
            setConfirmPhoto(null);
          }}
          onConfirm={() => {
            removePhoto.mutate(
              {
                path: { account_id: account.account_id },
                body: {
                  photo_id: confirmPhoto.photo_id,
                  access_hash: confirmPhoto.access_hash,
                  file_reference: confirmPhoto.file_reference,
                },
              },
              { onSuccess: refresh },
            );
          }}
        />
      ) : null}
      {confirmStory ? (
        <ConfirmModal
          title={t('accounts.profile.removeStoryTitle')}
          body={t('accounts.profile.removeStoryBody')}
          confirmLabel={t('accounts.profile.removeStoryConfirm')}
          cancelLabel={t('accounts.profile.cancel')}
          onClose={() => {
            setConfirmStory(null);
          }}
          onConfirm={() => {
            removeStory.mutate(
              {
                path: { account_id: account.account_id },
                body: { story_id: confirmStory.story_id },
              },
              { onSuccess: refresh },
            );
          }}
        />
      ) : null}
      {confirmMusic ? (
        <ConfirmModal
          title={t('accounts.profile.removeMusicTitle')}
          body={t('accounts.profile.removeMusicBody')}
          confirmLabel={t('accounts.profile.removeMusicConfirm')}
          cancelLabel={t('accounts.profile.cancel')}
          onClose={() => {
            setConfirmMusic(null);
          }}
          onConfirm={() => {
            removeMusic.mutate(
              {
                path: { account_id: account.account_id },
                body: {
                  file_id: confirmMusic.file_id,
                  access_hash: confirmMusic.access_hash ?? 0,
                  file_reference: confirmMusic.file_reference ?? '',
                },
              },
              { onSuccess: refresh },
            );
          }}
        />
      ) : null}
    </>
  );
}
