import { useForm, useStore } from '@tanstack/react-form';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { z } from 'zod';

import {
  accountProfileSnapshotQueryOptions,
  accountsQueryKey,
  addAccountMusicMutation,
  removeAccountMusicMutation,
  removeAccountPhotoMutation,
  removeAccountStoryMutation,
  setAccountPhotoMainMutation,
  setAccountPhotoMutation,
  setAccountStoryPinnedMutation,
  updateAccountProfileMutation,
} from '@/entities/account';
import type {
  AccountProfileView,
  AccountRead,
  ProfileMusicView,
  ProfilePhotoView,
  ProfileStoryView,
} from '@/shared/api';
import { ConfirmModal, FieldError, FormField, Modal, toastError } from '@/shared/ui';

import { AddStoryModal } from './AddStoryModal';
import { ChannelsTab } from './ChannelsTab';

// Telegram's real profile limits: non-empty first name ≤64, last name ≤64,
// bio ≤70, username 5–32 chars of [A-Za-z0-9_] starting with a letter
// ('' is allowed everywhere but first name — it clears the field).
const USERNAME_RE = /^[A-Za-z][A-Za-z0-9_]{4,31}$/;
const profileSchema = z.object({
  first_name: z
    .string()
    .trim()
    .min(1, 'accounts.profile.errFirstName')
    .max(64, 'accounts.profile.errFirstNameMax'),
  last_name: z.string().trim().max(64, 'accounts.profile.errLastNameMax'),
  username: z
    .string()
    .trim()
    .refine((value) => value === '' || USERNAME_RE.test(value), 'accounts.profile.errUsername'),
  bio: z.string().trim().max(70, 'accounts.profile.errBioMax'),
});

// The design's profile-edit modal: hero header, a 5-tab segmented header
// (text / photo / stories / music / channels), per-tab bodies, and a
// save→saved swap footer. Every tab is wired to /api/v1: Текст persists the
// profile, the photo / stories / music tabs render the account's live media
// (the profile-snapshot view) with real upload + remove, and the channels tab
// manages the account's own channels (its own queries — outside the snapshot
// busy scrim).
type Tab = 'text' | 'photo' | 'stories' | 'music' | 'channels';

const FIELD =
  'tb-time w-full rounded-[10px] border border-line-input bg-white px-3 py-[9px] text-[13px] outline-none';
const LABEL = 'mb-[6px] block text-[12px] font-medium text-[#3a3a3a]';
// Fallback tile background when a media item carries no thumbnail.
const TILE = 'linear-gradient(135deg,#cfd8ec,#e7dfd2)';
// Client-side mirror of the backend photo gate (services/accounts/_uploads.py
// suffix allowlist + settings.profile_media.photo_max_bytes default) so a
// GIF/HEIC/oversized file is rejected up front instead of uploading fully and
// failing with an untranslated 400.
const PHOTO_SUFFIXES = ['.jpg', '.jpeg', '.png', '.webp'];
const PHOTO_MAX_BYTES = 10_000_000;

function isUploadablePhoto(file: File): boolean {
  const name = file.name.toLowerCase();
  return file.size <= PHOTO_MAX_BYTES && PHOTO_SUFFIXES.some((suffix) => name.endsWith(suffix));
}

function DashedAdd({
  ratio,
  label,
  onClick,
  busy = false,
  disabled = false,
}: {
  ratio: string;
  label: string;
  onClick: () => void;
  busy?: boolean;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      disabled={busy || disabled}
      onClick={onClick}
      style={{ aspectRatio: ratio }}
      className="flex flex-col items-center justify-center gap-[6px] rounded-[12px] border-[1.5px] border-dashed border-[#d2d0cc] bg-white text-[12px] font-medium text-ink-muted disabled:opacity-60"
    >
      {busy ? (
        <span className="tb-spin inline-block h-[18px] w-[18px] rounded-full border-2 border-line-input border-t-primary" />
      ) : (
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
      )}
      {label}
    </button>
  );
}

function dedupeById(photos: ProfilePhotoView[]): ProfilePhotoView[] {
  const seen = new Set<string>();
  return photos.filter((photo) => {
    if (seen.has(photo.photo_id)) return false;
    seen.add(photo.photo_id);
    return true;
  });
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
  const setMainPhoto = useMutation(setAccountPhotoMainMutation());
  const addMusic = useMutation(addAccountMusicMutation());
  const removeStory = useMutation(removeAccountStoryMutation());
  const setStoryPinned = useMutation(setAccountStoryPinnedMutation());
  const removeMusic = useMutation(removeAccountMusicMutation());
  const removePhoto = useMutation(removeAccountPhotoMutation());
  const photoInput = useRef<HTMLInputElement>(null);
  const musicInput = useRef<HTMLInputElement>(null);

  const snapOpts = accountProfileSnapshotQueryOptions({
    path: { account_id: account.account_id },
  });
  const snapshot = useQuery(snapOpts);
  // «Обновить» outcome: spin while loading, then flash a green ✓ / red ✗.
  const [refreshState, setRefreshState] = useState<'idle' | 'loading' | 'ok' | 'error'>('idle');
  // The post-action background re-pull is fire-and-forget; this drives the
  // content-body overlay so a media edit doesn't look frozen while it settles.
  const [syncing, setSyncing] = useState(false);
  // A failed background re-pull rejects on the refresh:true query key, which
  // loadError (watching the plain key) can't see — track it here so the banner
  // shows instead of silently presenting a pre-mutation grid as current.
  const [syncError, setSyncError] = useState(false);
  // Re-render every 30s so the "Обновлено N мин назад" label keeps advancing —
  // it's derived from Date.now() and would otherwise freeze on "только что".
  const [, setNowTick] = useState(0);
  useEffect(() => {
    const id = window.setInterval(() => {
      setNowTick((n) => n + 1);
    }, 30_000);
    return () => {
      window.clearInterval(id);
    };
  }, []);
  // Defensive dedup by photo_id: Telegram can momentarily echo a duplicate id
  // during a make-main promotion, and a repeated tile would misrender.
  const photos = dedupeById(snapshot.data?.photos ?? []);
  const stories = snapshot.data?.stories ?? [];
  const music = snapshot.data?.music ?? [];
  // A transport failure (snapshot.isError) or a Telegram refusal (200 carrying
  // `error`) must show an explicit error + retry — otherwise the media tabs
  // render empty and read as "this account has no photos/stories/music".
  const loadError = snapshot.isError || Boolean(snapshot.data?.error) || syncError;
  // Older Telethon builds lack the saved-music TL methods; the snapshot flags
  // that so the UI shows an "unsupported" note instead of a picker that fails.
  const musicSupported = snapshot.data?.music_supported !== false;
  // Force a live Telegram re-pull (bypasses the 30s read-cache) and write it into
  // the rendered snapshot query. Shared by «Обновить» and every post-mutation
  // refresh — an invalidate alone can hit the TTL cache and return a stale photo
  // set (the make-main duplicate/loss bug surfaced exactly here).
  const forcePull = () =>
    queryClient.fetchQuery(
      accountProfileSnapshotQueryOptions({
        path: { account_id: account.account_id },
        query: { refresh: true },
      }),
    );
  // Scoped: this account's snapshot + the accounts table (name/username/avatar
  // show in the list) — not the whole cache.
  const refresh = () => {
    setSyncing(true);
    void forcePull()
      .then((fresh) => {
        queryClient.setQueryData(snapOpts.queryKey, fresh);
        setSyncError(false);
      })
      .catch(() => {
        setSyncError(true);
      })
      .finally(() => {
        setSyncing(false);
      });
    void queryClient.invalidateQueries({ queryKey: accountsQueryKey() });
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
  const [photoProgress, setPhotoProgress] = useState<{ done: number; total: number } | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [storyOpen, setStoryOpen] = useState(false);
  const [saved, setSaved] = useState(false);
  const [confirmPhoto, setConfirmPhoto] = useState<ProfilePhotoView | null>(null);
  const [confirmStory, setConfirmStory] = useState<ProfileStoryView | null>(null);
  const [confirmMusic, setConfirmMusic] = useState<ProfileMusicView | null>(null);

  // A single "media in flight" flag: any photo/story/music write plus the
  // post-action background sync. Drives the content-body overlay and disables
  // the media controls. Excludes the text Save (footer has its own spinner).
  const busy =
    syncing ||
    Boolean(photoProgress) ||
    setMainPhoto.isPending ||
    removePhoto.isPending ||
    removeStory.isPending ||
    setStoryPinned.isPending ||
    addMusic.isPending ||
    removeMusic.isPending;

  const form = useForm({
    defaultValues: {
      first_name: account.first_name ?? '',
      last_name: account.last_name ?? '',
      username: account.username ?? '',
      bio: account.bio ?? '',
    },
    validators: { onChange: profileSchema, onMount: profileSchema },
    onSubmit: ({ value }) => {
      updateProfile.mutate(
        {
          // Contract: '' CLEARS a field on Telegram, null means "leave
          // unchanged" — the form always submits explicit (trimmed) strings.
          body: {
            account_id: account.account_id,
            first_name: value.first_name.trim(),
            last_name: value.last_name.trim(),
            username: value.username.trim(),
            bio: value.bio.trim(),
          },
        },
        {
          onSuccess: () => {
            // Reset the baseline to the just-saved values so the form is no
            // longer "dirty" — otherwise closing afterwards wrongly prompts
            // "discard unsaved edits?" even though everything was saved.
            form.reset(value);
            setSaved(true);
            window.setTimeout(() => {
              setSaved(false);
            }, 1400);
            refresh();
          },
        },
      );
    },
  });
  const canSave = useStore(form.store, (state) => state.canSubmit);
  const isDirty = useStore(form.store, (state) => state.isDirty);

  // Seed the text fields from a successfully-pulled live profile ('' for unset
  // fields), without marking the form dirty. first_name can't be empty on
  // Telegram, so a null there means "no text in this snapshot" — keep ours.
  const seedForm = useCallback(
    (view: AccountProfileView) => {
      if (view.error) return;
      if (view.first_name != null) {
        form.setFieldValue('first_name', view.first_name, { dontUpdateMeta: true });
      }
      form.setFieldValue('last_name', view.last_name ?? '', { dontUpdateMeta: true });
      form.setFieldValue('username', view.username ?? '', { dontUpdateMeta: true });
      form.setFieldValue('bio', view.bio ?? '', { dontUpdateMeta: true });
    },
    [form],
  );

  // The row snapshot the modal opened with can lag Telegram; once the live
  // profile arrives, re-seed the fields — but never clobber user edits.
  const snapshotData = snapshot.data;
  useEffect(() => {
    if (snapshotData && !form.state.isDirty) seedForm(snapshotData);
  }, [snapshotData, form, seedForm]);

  // «Обновить»: force a live re-pull (bypasses the read cache), write it into the
  // rendered snapshot, and reseed the header + text fields from the fresh profile.
  const onRefresh = async () => {
    setRefreshState('loading');
    try {
      const fresh = await forcePull();
      queryClient.setQueryData(snapOpts.queryKey, fresh);
      setSyncError(false);
      seedForm(fresh);
      // A 200 carrying an `error` field means Telegram refused the live pull —
      // that's a failed refresh, not a success.
      setRefreshState(fresh.error ? 'error' : 'ok');
    } catch {
      setRefreshState('error');
    } finally {
      window.setTimeout(() => {
        setRefreshState('idle');
      }, 1400);
    }
  };

  // Escape / backdrop / × ask before discarding unsaved text edits.
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const requestClose = () => {
    if (form.state.isDirty) setConfirmDiscard(true);
    else onClose();
  };

  // Header reflects the live snapshot (falls back to the stored account row).
  const liveFirst = snapshot.data?.first_name ?? account.first_name;
  const liveLast = snapshot.data?.last_name ?? account.last_name;
  const liveUser = snapshot.data?.username ?? account.username;
  // The current avatar is the photo Telegram flags as main (by id, authoritative
  // — not the history's index 0); #227 serves its thumbnail from the cacheable
  // image endpoint (thumb_url), not inline data.
  const avatarUri = (photos.find((photo) => photo.is_main) ?? photos[0])?.thumb_url ?? undefined;
  const initial = (liveFirst ?? account.phone ?? account.account_id).trim().charAt(0).toUpperCase();
  const fullName =
    [liveFirst, liveLast].filter(Boolean).join(' ') || (account.phone ?? account.account_id);

  // Bulk profile-photo upload. Sequential on purpose: each uploadProfilePhoto
  // becomes the account's current avatar and Telegram orders the photo history
  // by upload time, so parallel uploads on one session would race on ordering
  // and invite FLOOD_WAIT. One-at-a-time keeps the pick order (last file ends
  // up as the main avatar) and is gentle on the session. A rejected file is
  // skipped — the global mutation-error toast reports it — so one bad image
  // doesn't abort the batch. Snapshot refreshes once, after the batch.
  const uploadPhotos = async (files: File[]) => {
    // Prefilter by the backend's own suffix/size gate: a file it would 400 is
    // rejected here with a translated toast instead of uploading fully first.
    const uploadable: File[] = [];
    for (const file of files) {
      if (isUploadablePhoto(file)) {
        uploadable.push(file);
      } else {
        toastError(
          t('accounts.profile.photoRejected', { name: file.name, mb: PHOTO_MAX_BYTES / 1_000_000 }),
        );
      }
    }
    if (!uploadable.length) return;
    setPhotoProgress({ done: 0, total: uploadable.length });
    for (const [index, file] of uploadable.entries()) {
      try {
        await setPhoto.mutateAsync({ body: { account_id: account.account_id, file } });
      } catch {
        // reported by the global mutation-error toast; keep going
      }
      setPhotoProgress({ done: index + 1, total: uploadable.length });
    }
    setPhotoProgress(null);
    refresh();
  };
  const onPhotosPicked = (event: React.ChangeEvent<HTMLInputElement>) => {
    // Materialise the array BEFORE resetting the input — event.target.files is
    // a live FileList, and value='' empties it, so reading it afterwards yields
    // nothing. (jsdom doesn't emulate that clear, which is why tests missed it.)
    const files = Array.from(event.target.files ?? []);
    event.target.value = '';
    void uploadPhotos(files);
  };

  const onMusicPicked = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    addMusic.mutate(
      { path: { account_id: account.account_id }, body: { file } },
      // Settled, not success: a failure has already invalidated the server-side
      // snapshot cache, so the grid must re-pull either way or it keeps serving
      // ids Telegram has since replaced.
      { onSettled: refresh },
    );
    event.target.value = '';
  };

  const tabBtn = (value: Tab): string =>
    `border-b-2 py-[14px] text-[13px] font-medium transition-colors ${tab === value ? 'border-primary text-ink' : 'border-transparent text-ink-muted'}`;

  return (
    <>
      <Modal onClose={requestClose} z={70} className="w-[580px]">
        <div className="flex max-h-[88vh] flex-col overflow-hidden">
          {/* header */}
          <div className="flex items-center gap-[14px] border-b border-[#f0eeeb] px-5 py-[18px]">
            <div
              className="flex h-[52px] w-[52px] shrink-0 items-center justify-center overflow-hidden rounded-full bg-gradient-to-br from-[#7c9cff] to-[#a0e0c0] text-[20px] font-semibold text-white"
              style={
                avatarUri
                  ? {
                      backgroundImage: `url(${avatarUri})`,
                      backgroundSize: 'cover',
                      backgroundPosition: 'center',
                    }
                  : undefined
              }
            >
              {avatarUri ? '' : initial}
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
                disabled={refreshState === 'loading'}
                onClick={() => {
                  void onRefresh();
                }}
                className={`inline-flex items-center gap-[6px] rounded-full border bg-white px-3 py-[6px] text-[12.5px] font-medium transition-colors disabled:opacity-70 ${
                  refreshState === 'ok'
                    ? 'border-[#bfe4cc] text-[#2e9e64]'
                    : refreshState === 'error'
                      ? 'border-[#f0c9c5] text-danger'
                      : 'border-line-input text-ink hover:border-[#bfd6ff] hover:text-primary'
                }`}
              >
                {refreshState === 'ok' ? (
                  <span className="tb-swapin inline-flex">
                    <svg
                      width="13"
                      height="13"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2.4"
                    >
                      <path d="M20 6 9 17l-5-5" />
                    </svg>
                  </span>
                ) : refreshState === 'error' ? (
                  <span className="tb-swapin inline-flex">
                    <svg
                      width="13"
                      height="13"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2.4"
                    >
                      <path d="M18 6 6 18M6 6l12 12" />
                    </svg>
                  </span>
                ) : (
                  <span className={`inline-flex ${refreshState === 'loading' ? 'tb-spin' : ''}`}>
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
                )}
                {refreshState === 'ok'
                  ? t('accounts.profile.refreshOk')
                  : refreshState === 'error'
                    ? t('accounts.profile.refreshError')
                    : t('accounts.profile.refresh')}
              </button>
              <span className="text-[11px] text-ink-subtle">{syncLabel}</span>
            </div>
            <button
              type="button"
              onClick={requestClose}
              aria-label={t('accounts.profile.close')}
              className="ml-[2px] h-[30px] w-[30px] shrink-0 rounded-full border border-line bg-white text-[16px] text-ink-muted"
            >
              ×
            </button>
          </div>

          {/* tabs */}
          <div className="flex gap-5 border-b border-[#f0eeeb] px-5">
            {(['text', 'photo', 'stories', 'music', 'channels'] as const).map((value) => (
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
          <div className="tb-scroll relative flex-1 overflow-y-auto p-5">
            {/* Applying overlay: every media edit calls refresh(), which re-pulls
                the snapshot from Telegram in the background. A greyed scrim with a
                spinner signals "still working" and blocks input to stop double-
                submits. It sits inside the overflow container, so `inset-0` pins it
                to the visible viewport rather than scrolling away. The text tab is
                excluded — its Save keeps the footer's own spinner/✓ — and so is
                the channels tab, which runs on its own queries. */}
            {busy && tab !== 'text' && tab !== 'channels' && (
              <div
                role="status"
                aria-live="polite"
                aria-label={t('accounts.profile.syncing')}
                className="absolute inset-0 z-20 flex flex-col items-center justify-center gap-3 bg-black/10 [animation:ovfade_0.2s_ease]"
              >
                <span className="tb-spin inline-block h-8 w-8 rounded-full border-[3px] border-line-input border-t-primary" />
                <span className="text-[12px] font-medium text-ink-muted">
                  {photoProgress
                    ? t('accounts.profile.uploadingCount', photoProgress)
                    : t('accounts.profile.syncing')}
                </span>
              </div>
            )}
            {loadError && tab !== 'channels' && (
              <div className="mb-4 flex items-center justify-between gap-3 rounded-[10px] border border-[#f0c9c5] bg-danger-tint px-3 py-[10px] text-[12.5px] text-danger">
                <span>{t('accounts.profile.loadError')}</span>
                <button
                  type="button"
                  disabled={refreshState === 'loading'}
                  onClick={() => {
                    void onRefresh();
                  }}
                  className="shrink-0 rounded-full border border-[#f0c9c5] bg-white px-3 py-[4px] text-[12px] font-medium disabled:opacity-60"
                >
                  {t('accounts.profile.refresh')}
                </button>
              </div>
            )}
            {tab === 'text' && (
              <div className="flex flex-col gap-[14px]">
                <div className="grid grid-cols-2 gap-3">
                  <form.Field name="first_name">
                    {(field) => <FormField field={field} label={t('accounts.profile.firstName')} />}
                  </form.Field>
                  <form.Field name="last_name">
                    {(field) => <FormField field={field} label={t('accounts.profile.lastName')} />}
                  </form.Field>
                </div>
                <form.Field name="username">
                  {(field) => (
                    <label className="block">
                      <span className={LABEL}>{t('accounts.profile.username')}</span>
                      <div className="relative flex items-center">
                        <span className="absolute left-3 text-[13px] text-ink-subtle">@</span>
                        <input
                          value={field.state.value}
                          onChange={(event) => {
                            field.handleChange(event.target.value);
                          }}
                          onBlur={field.handleBlur}
                          className={`${FIELD} pl-[26px]`}
                        />
                      </div>
                      <FieldError field={field} />
                    </label>
                  )}
                </form.Field>
                <form.Field name="bio">
                  {(field) => (
                    <label className="block">
                      <span className={LABEL}>{t('accounts.profile.bio')}</span>
                      <textarea
                        rows={3}
                        value={field.state.value}
                        onChange={(event) => {
                          field.handleChange(event.target.value);
                        }}
                        onBlur={field.handleBlur}
                        className={`${FIELD} resize-none [font-family:inherit]`}
                      />
                      <FieldError field={field} />
                    </label>
                  )}
                </form.Field>
              </div>
            )}

            {tab === 'photo' && (
              <div
                onDragOver={(event) => {
                  event.preventDefault();
                  setDragOver(true);
                }}
                onDragLeave={(event) => {
                  // Only clear on a real exit — hovering a child tile fires
                  // dragleave on the container and would otherwise flicker.
                  if (!event.currentTarget.contains(event.relatedTarget as Node))
                    setDragOver(false);
                }}
                onDrop={(event) => {
                  event.preventDefault();
                  setDragOver(false);
                  // Ignore a second drop while a batch is still uploading.
                  if (photoProgress) return;
                  const images = Array.from(event.dataTransfer.files).filter((file) =>
                    file.type.startsWith('image/'),
                  );
                  void uploadPhotos(images);
                }}
                className={`relative rounded-[12px] border-[1.5px] border-dashed p-3 transition-colors ${dragOver ? 'border-primary' : 'border-transparent'}`}
              >
                {dragOver && (
                  <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center rounded-[12px] bg-white/70 text-[13px] font-medium text-primary">
                    {t('accounts.profile.dropPhotos')}
                  </div>
                )}
                <div className="mb-3 text-[12px] text-ink-subtle">
                  {t('accounts.profile.photoHint')}
                </div>
                <div className="grid grid-cols-[repeat(auto-fill,minmax(104px,1fr))] gap-3">
                  {photos.map((photo) => (
                    <div key={photo.photo_id} className="relative">
                      <div
                        className="rounded-[12px] border border-black/5"
                        style={tileStyle(photo.thumb_url, '1')}
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
                      {photo.is_main ? (
                        <span className="mt-[6px] block w-full py-[2px] text-[11px] font-medium text-primary">
                          {t('accounts.profile.mainPhoto')}
                        </span>
                      ) : (
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => {
                            setMainPhoto.mutate(
                              {
                                path: { account_id: account.account_id },
                                body: {
                                  photo_id: photo.photo_id,
                                  access_hash: photo.access_hash,
                                  file_reference: photo.file_reference,
                                },
                              },
                              // Settled: make-main REPLACES the photo id on
                              // Telegram; after a failure the old id may be
                              // dead, so the grid must re-pull either way.
                              { onSettled: refresh },
                            );
                          }}
                          className="mt-[6px] block w-full py-[2px] text-left text-[11px] font-medium text-primary hover:underline disabled:opacity-50"
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
                        style={tileStyle(story.thumb_url, '9 / 16')}
                      />
                      {(story.views != null || story.reactions != null) && (
                        <span className="absolute left-[5px] top-[5px] inline-flex items-center gap-[6px] rounded-[6px] bg-[rgba(11,11,12,0.6)] px-[5px] py-[2px] text-[9px] font-medium text-white">
                          {story.views != null && (
                            <span
                              title={t('accounts.profile.storyViews', { n: story.views })}
                              className="inline-flex items-center gap-[3px]"
                            >
                              <svg
                                width="10"
                                height="10"
                                viewBox="0 0 24 24"
                                fill="none"
                                stroke="currentColor"
                                strokeWidth="2"
                              >
                                <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
                                <circle cx="12" cy="12" r="3" />
                              </svg>
                              {story.views}
                            </span>
                          )}
                          {story.reactions != null && (
                            <span
                              title={t('accounts.profile.storyReactions', { n: story.reactions })}
                              className="inline-flex items-center gap-[3px]"
                            >
                              <svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor">
                                <path d="M12 21l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.18L12 21z" />
                              </svg>
                              {story.reactions}
                            </span>
                          )}
                        </span>
                      )}
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
                      <button
                        type="button"
                        disabled={setStoryPinned.isPending}
                        aria-label={t(
                          story.is_pinned
                            ? 'accounts.profile.unpinStory'
                            : 'accounts.profile.pinStory',
                        )}
                        onClick={() => {
                          setStoryPinned.mutate(
                            {
                              path: { account_id: account.account_id },
                              body: { story_id: story.story_id, pinned: !story.is_pinned },
                            },
                            { onSettled: refresh },
                          );
                        }}
                        className={`absolute inset-x-[5px] bottom-[24px] truncate rounded-[6px] px-[5px] py-[2px] text-center text-[9px] font-medium disabled:opacity-50 ${
                          story.is_pinned
                            ? 'bg-primary text-white'
                            : 'bg-[rgba(11,11,12,0.6)] text-white'
                        }`}
                      >
                        {t(
                          story.is_pinned
                            ? 'accounts.profile.pinnedForever'
                            : 'accounts.profile.pin24h',
                        )}
                      </button>
                      <span className="absolute inset-x-[5px] bottom-[5px] truncate rounded-[6px] bg-[rgba(11,11,12,0.6)] px-[5px] py-[2px] text-center text-[9px] font-medium text-white">
                        {t(`accounts.addStory.${story.privacy_preset ?? 'unknown'}`)}
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

            {tab === 'music' && !musicSupported && (
              <div className="rounded-[12px] border border-dashed border-line bg-white px-4 py-6 text-center text-[12.5px] text-ink-subtle">
                {t('accounts.profile.musicUnsupported')}
              </div>
            )}

            {tab === 'music' && musicSupported && (
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
            )}

            {tab === 'channels' && <ChannelsTab accountId={account.account_id} />}
          </div>

          {/* footer */}
          <div className="flex justify-end gap-2 border-t border-[#f0eeeb] px-5 py-[14px]">
            <button
              type="button"
              onClick={requestClose}
              className="rounded-full border border-line-input bg-white px-[18px] py-[9px] text-[13px] font-medium text-ink"
            >
              {t('accounts.profile.cancel')}
            </button>
            <button
              type="button"
              onClick={() => {
                void form.handleSubmit();
              }}
              disabled={updateProfile.isPending || !canSave || !isDirty}
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
          onConfirm={() =>
            removePhoto
              .mutateAsync({
                path: { account_id: account.account_id },
                body: {
                  photo_id: confirmPhoto.photo_id,
                  access_hash: confirmPhoto.access_hash,
                  file_reference: confirmPhoto.file_reference,
                },
              })
              // finally, not then: a failed remove has already invalidated the
              // server-side cache, so the grid must re-pull or it keeps dead
              // ids; the rejection still propagates so the dialog stays open.
              .finally(refresh)
          }
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
          onConfirm={() =>
            removeStory
              .mutateAsync({
                path: { account_id: account.account_id },
                body: { story_id: confirmStory.story_id },
              })
              .finally(refresh)
          }
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
          onConfirm={() =>
            removeMusic
              .mutateAsync({
                path: { account_id: account.account_id },
                body: {
                  file_id: confirmMusic.file_id,
                  access_hash: confirmMusic.access_hash ?? '0',
                  file_reference: confirmMusic.file_reference ?? '',
                },
              })
              .finally(refresh)
          }
        />
      ) : null}
      {confirmDiscard ? (
        <ConfirmModal
          title={t('accounts.profile.discardTitle')}
          body={t('accounts.profile.discardBody')}
          confirmLabel={t('accounts.profile.discardConfirm')}
          cancelLabel={t('accounts.profile.cancel')}
          onClose={() => {
            setConfirmDiscard(false);
          }}
          onConfirm={onClose}
        />
      ) : null}
    </>
  );
}
