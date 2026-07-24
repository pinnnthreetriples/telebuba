import { useForm, useStore } from '@tanstack/react-form';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { z } from 'zod';

import {
  accountProfileSnapshotQueryOptions,
  accountsQueryKey,
  addAccountMusicMutation,
  fetchLiveProfileSnapshot,
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
  MusicRemoveRequest,
  ProfilePhotoView,
  ProfileStoryView,
} from '@/shared/api';
import { ConfirmModal, FormField, Modal, toastError } from '@/shared/ui';

import { isUploadablePhoto, PHOTO_MAX_BYTES } from './_channelsShared';
import { dedupeById, profileErrorField, profileErrorText } from './_profileShared';
import { FIELD } from './_styles';
import { AddStoryModal } from './AddStoryModal';
import { ChannelsTab } from './ChannelsTab';
import { MusicTab } from './MusicTab';
import { PhotoTab } from './PhotoTab';
import { StoriesTab } from './StoriesTab';

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

// "Обновлено {только что | N мин назад}" from the snapshot query's last fetch.
// Its own component with its own 30s tick, so only this label re-renders while
// the minutes advance — not the whole modal. (Derived from Date.now(); without
// the tick it would freeze on "только что".)
function SyncLabel({ updatedAt }: { updatedAt: number }) {
  const { t } = useTranslation();
  const [, setNowTick] = useState(0);
  useEffect(() => {
    const id = window.setInterval(() => {
      setNowTick((n) => n + 1);
    }, 30_000);
    return () => {
      window.clearInterval(id);
    };
  }, []);
  const mins = updatedAt ? Math.floor((Date.now() - updatedAt) / 60000) : 0;
  return (
    <span className="text-[11px] text-ink-subtle">
      {!updatedAt || mins < 1
        ? t('accounts.profile.updatedJustNow')
        : t('accounts.profile.updatedMinAgo', { n: mins })}
    </span>
  );
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

  const snapOpts = accountProfileSnapshotQueryOptions({
    path: { account_id: account.account_id },
  });
  const snapshot = useQuery(snapOpts);
  // «Обновить» outcome: spin while loading, then flash a green ✓ / red ✗.
  const [refreshState, setRefreshState] = useState<'idle' | 'loading' | 'ok' | 'error'>('idle');
  // The post-action background re-pull is fire-and-forget; this drives the
  // content-body overlay so a media edit doesn't look frozen while it settles.
  const [syncing, setSyncing] = useState(false);
  // A failed background re-pull rejects outside any rendered query, which
  // loadError (watching the plain key) can't see — track it here so the banner
  // shows instead of silently presenting a pre-mutation grid as current.
  const [syncError, setSyncError] = useState(false);
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
  // Serialised force-pull: a live Telegram re-pull (bypasses the 30s read
  // cache, straight through the SDK so no refresh:true twin lands in the query
  // cache) written into the rendered snapshot query. Shared by «Обновить» and
  // every post-mutation refresh. Every call starts a fresh pull — reusing an
  // in-flight one could serve pre-mutation data — but only the LATEST one
  // writes: an older pull resolving last must not clobber newer data
  // (superseded calls return null).
  const pullGen = useRef(0);
  const forcePull = async (): Promise<AccountProfileView | null> => {
    const gen = ++pullGen.current;
    try {
      const fresh = await fetchLiveProfileSnapshot(account.account_id);
      if (gen !== pullGen.current) return null;
      queryClient.setQueryData(snapOpts.queryKey, fresh);
      setSyncError(false);
      return fresh;
    } catch (error) {
      if (gen !== pullGen.current) return null;
      throw error;
    } finally {
      // Whichever pull is latest clears the overlay — success or failure.
      if (gen === pullGen.current) setSyncing(false);
    }
  };
  // Scoped: this account's snapshot + the accounts table (name/username/avatar
  // show in the list) — not the whole cache.
  const refresh = () => {
    setSyncing(true);
    void forcePull().catch(() => {
      setSyncError(true);
    });
    void queryClient.invalidateQueries({ queryKey: accountsQueryKey() });
  };

  const [tab, setTab] = useState<Tab>('text');
  const [photoProgress, setPhotoProgress] = useState<{ done: number; total: number } | null>(null);
  const [storyOpen, setStoryOpen] = useState(false);
  const [saved, setSaved] = useState(false);
  const [confirmPhoto, setConfirmPhoto] = useState<ProfilePhotoView | null>(null);
  const [confirmStory, setConfirmStory] = useState<ProfileStoryView | null>(null);
  const [confirmMusic, setConfirmMusic] = useState<MusicRemoveRequest | null>(null);

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

  // A rejected save carries a stable code in the error envelope; username/bio
  // codes render under their field, the rest in a general box. Unknown codes
  // show as-is (plus the global mutation toast — same contract as channels).
  const saveError = updateProfile.isError ? updateProfile.error : null;
  const saveErrorField = saveError ? profileErrorField(saveError) : null;
  const saveErrorText = saveError
    ? profileErrorText(saveError, t, t('accounts.profile.saveError'))
    : null;

  // onMount validation already flags an empty stored first name, but errors
  // only render for touched fields — mark it touched so the reason Save is
  // disabled shows instead of a silently dead button.
  useEffect(() => {
    if (form.getFieldValue('first_name').trim() === '') {
      form.setFieldMeta('first_name', (meta) => ({ ...meta, isTouched: true }));
    }
  }, [form]);

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
      if (fresh) {
        seedForm(fresh);
        // A 200 carrying an `error` field means Telegram refused the live pull —
        // that's a failed refresh, not a success.
        setRefreshState(fresh.error ? 'error' : 'ok');
      } else {
        // Superseded by a newer pull — that one reports the outcome.
        setRefreshState('idle');
      }
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
                disabled={refreshState === 'loading' || syncing}
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
              <SyncLabel updatedAt={snapshot.dataUpdatedAt} />
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
                  disabled={refreshState === 'loading' || syncing}
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
                    <FormField field={field} label={t('accounts.profile.username')}>
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
                      {saveErrorField === 'username' && saveErrorText != null && (
                        <span className="mt-[5px] block text-[11px] font-medium text-[#c0473f]">
                          {saveErrorText}
                        </span>
                      )}
                    </FormField>
                  )}
                </form.Field>
                <form.Field name="bio">
                  {(field) => (
                    <FormField field={field} label={t('accounts.profile.bio')}>
                      <textarea
                        rows={3}
                        value={field.state.value}
                        onChange={(event) => {
                          field.handleChange(event.target.value);
                        }}
                        onBlur={field.handleBlur}
                        className={`${FIELD} resize-none [font-family:inherit]`}
                      />
                      {saveErrorField === 'bio' && saveErrorText != null && (
                        <span className="mt-[5px] block text-[11px] font-medium text-[#c0473f]">
                          {saveErrorText}
                        </span>
                      )}
                    </FormField>
                  )}
                </form.Field>
                {saveErrorField === null && saveErrorText != null && (
                  <div className="rounded-[10px] border border-[#f0c9c5] bg-danger-tint px-3 py-[10px] text-[12.5px] text-danger">
                    {saveErrorText}
                  </div>
                )}
              </div>
            )}

            {tab === 'photo' && (
              <PhotoTab
                photos={photos}
                busy={busy}
                uploading={Boolean(photoProgress)}
                onUpload={(files) => {
                  void uploadPhotos(files);
                }}
                onRemove={setConfirmPhoto}
                onMakeMain={(photo) => {
                  setMainPhoto.mutate(
                    {
                      path: { account_id: account.account_id },
                      body: {
                        photo_id: photo.photo_id,
                        access_hash: photo.access_hash,
                        file_reference: photo.file_reference,
                      },
                    },
                    // Settled: make-main RE-UPLOADS the photo as a new one
                    // (fresh id at the front, the original stays as a visible
                    // duplicate the operator may delete), so the grid must
                    // re-pull either way.
                    { onSettled: refresh },
                  );
                }}
              />
            )}

            {tab === 'stories' && (
              <StoriesTab
                stories={stories}
                pinPending={setStoryPinned.isPending}
                onAdd={() => {
                  setStoryOpen(true);
                }}
                onRemove={setConfirmStory}
                onPinToggle={(story) => {
                  setStoryPinned.mutate(
                    {
                      path: { account_id: account.account_id },
                      body: { story_id: story.story_id, pinned: !story.is_pinned },
                    },
                    { onSettled: refresh },
                  );
                }}
              />
            )}

            {tab === 'music' && (
              <MusicTab
                music={music}
                supported={musicSupported}
                busy={busy}
                onPick={(file) => {
                  addMusic.mutate(
                    { path: { account_id: account.account_id }, body: { file } },
                    // Settled, not success: a failure has already invalidated
                    // the server-side snapshot cache, so the grid must re-pull
                    // either way or it keeps serving ids Telegram has since
                    // replaced.
                    { onSettled: refresh },
                  );
                }}
                onRemove={(track) => {
                  // The remove button is disabled without a file_reference;
                  // the guard keeps the narrowing honest (no '' fallback ever
                  // reaches the wire).
                  if (!track.file_reference) return;
                  setConfirmMusic({
                    file_id: track.file_id,
                    access_hash: track.access_hash ?? '0',
                    file_reference: track.file_reference,
                  });
                }}
              />
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
                body: confirmMusic,
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
