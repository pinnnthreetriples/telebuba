import { useForm, useStore } from '@tanstack/react-form';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { z } from 'zod';

import {
  accountPrivacyQueryKey,
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
import { resyncAccountAvatar } from '@/shared/api';
import type { AccountProfileView, AccountRead, MusicRemoveRequest } from '@/shared/api';
import {
  Button,
  ConfirmModal,
  FormField,
  Icon,
  IconButton,
  Input,
  Modal,
  Notice,
  Textarea,
  toastError,
} from '@/shared/ui';

import { isUploadablePhoto, PHOTO_MAX_BYTES } from './_channelsShared';
import { dedupeById, profileCodeText, profileErrorField, profileErrorText } from './_profileShared';
import { AddStoryModal } from './AddStoryModal';
import { ChannelsTab } from './ChannelsTab';
import { MusicTab } from './MusicTab';
import { PhotoTab } from './PhotoTab';
import { PrivacyTab } from './PrivacyTab';
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

// The design's profile-edit modal: hero header, a 6-tab segmented header
// (text / photo / stories / music / channels / privacy), per-tab bodies, and a
// save→saved swap footer. Every tab is wired to /api/v1: Текст persists the
// profile, the photo / stories / music tabs render the account's live media
// (the profile-snapshot view) with real upload + remove, and the channels and
// privacy tabs manage the account's own channels and its Telegram privacy
// levels (their own queries — outside the snapshot busy scrim).
type Tab = 'text' | 'photo' | 'stories' | 'music' | 'channels' | 'privacy';
const TABS = [
  'text',
  'photo',
  'stories',
  'music',
  'channels',
  'privacy',
] as const satisfies readonly Tab[];

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
  // 0 = never fetched (dataUpdatedAt before the first success). Saying "Обновлено
  // только что" about data that has not arrived yet is a confident false claim —
  // and it sat next to media tabs rendering their empty state, so render nothing
  // until there is a fetch to date.
  if (!updatedAt) return null;
  const mins = Math.floor((Date.now() - updatedAt) / 60000);
  return (
    <span className="type-caption">
      {mins < 1
        ? t('accounts.profile.updatedJustNow')
        : t('accounts.profile.updatedMinAgo', { n: mins })}
    </span>
  );
}

// «Обновить» in its three looks. idle and loading share the ↻ glyph (loading
// spins it); ok/error swap in a ✓/✗ and recolour the border. A 3-entry lookup
// instead of the same <svg> written out three times under nested ternaries.
const REFRESH_LOOK = {
  idle: {
    path: 'M21 2v6h-6M3 12a9 9 0 0 1 15-6.7L21 8M3 22v-6h6M21 12a9 9 0 0 1-15 6.7L3 16',
    stroke: '2',
    labelKey: 'accounts.profile.refresh',
    border: 'border-line text-content-primary hover:border-info-line hover:text-action-primary',
  },
  ok: {
    path: 'M20 6 9 17l-5-5',
    stroke: '2.4',
    labelKey: 'accounts.profile.refreshOk',
    border: 'border-success-line text-success-deep',
  },
  error: {
    path: 'M18 6 6 18M6 6l12 12',
    stroke: '2.4',
    labelKey: 'accounts.profile.refreshError',
    border: 'border-danger-line text-danger',
  },
} as const;

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
  // shows instead of silently presenting a pre-mutation grid as current. Holds
  // the rendered reason, not a flag: nothing else keeps that rejection.
  const [syncError, setSyncError] = useState<string | null>(null);
  const photos = dedupeById(snapshot.data?.photos ?? []);
  const stories = snapshot.data?.stories ?? [];
  const music = snapshot.data?.music ?? [];
  // A transport failure (snapshot.isError) or a Telegram refusal (200 carrying
  // `error`) must show an explicit error + retry — otherwise the media tabs
  // render empty and read as "this account has no photos/stories/music".
  //
  // WHY it failed decides what the operator does next — wait out a FloodWait,
  // re-login a dead session, fix a down proxy — so the reason is rendered, not
  // collapsed to a boolean. Two shapes reach here: a 200 whose `error` carries
  // the gateway's own label (services/accounts/profile_read.py) and a rejected
  // request carrying the error envelope's stable code. `noReason` is the same
  // "we don't know" copy the privacy tab uses for a rejection that isn't ours.
  const loadErrorReason =
    snapshot.data?.error != null
      ? profileCodeText(snapshot.data.error, t)
      : snapshot.isError
        ? profileErrorText(snapshot.error, t, t('accounts.profile.privacy.noReason'))
        : syncError;
  const loadError = loadErrorReason != null;
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
      // The plain (cacheable) read this modal mounted with is a separate
      // request against the same key. Left running, it resolves AFTER this
      // write and puts pre-mutation fields back — reverting the form and, for
      // a bio, raising a "Telegram did not keep it" warning about a value that
      // did land. Nothing re-pulls on its own afterwards, so drop it first.
      await queryClient.cancelQueries({ queryKey: snapOpts.queryKey });
      // Re-checked here, not before the cancel: the await is another window in
      // which a newer pull can start, and the older one must not write last.
      if (gen !== pullGen.current) return null;
      queryClient.setQueryData(snapOpts.queryKey, fresh);
      setSyncError(null);
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
    void forcePull().catch((error: unknown) => {
      setSyncError(profileErrorText(error, t, t('accounts.profile.privacy.noReason')));
    });
    void queryClient.invalidateQueries({ queryKey: accountsQueryKey() });
  };

  const [tab, setTab] = useState<Tab>('text');
  const [photoProgress, setPhotoProgress] = useState<{ done: number; total: number } | null>(null);
  const [storyOpen, setStoryOpen] = useState(false);
  const [saved, setSaved] = useState(false);
  // The bio the last successful save sent, or null if nothing was saved since
  // the modal opened / the field was edited again. Compared against the live
  // snapshot below — never used as a value to display or re-submit.
  //
  // Held in a ref as well as state, and the seeding guard below reads the REF.
  // Not for timing: making the guard read the state would mean adding it to
  // `seedForm`'s deps, and that re-runs the seeding effect on every verdict
  // change — against whatever snapshot is cached at that moment, which right
  // after a save is still the pre-save one, reverting the other fields to it.
  // A ref keeps `seedForm` stable so the effect fires only on a fresh snapshot.
  // The rendered verdict still needs the state, or nothing re-renders to show it.
  const savedBioRef = useRef<string | null>(null);
  const [savedBio, setSavedBio] = useState<string | null>(null);
  const rememberSavedBio = (bio: string | null) => {
    savedBioRef.current = bio;
    setSavedBio(bio);
  };
  // The three remove confirmations are one dialog: same four labels keyed off the
  // subject, same "run it, then re-pull either way" body. `kind` picks the copy,
  // `run` is the removal itself — `finally`, not `then`, because a failed remove
  // has already invalidated the server-side cache, so the grid must re-pull or it
  // keeps dead ids; the rejection still propagates so the dialog stays open.
  const [confirm, setConfirm] = useState<{
    kind: 'Photo' | 'Story' | 'Music';
    run: () => Promise<unknown>;
  } | null>(null);

  // A single "the media on screen isn't the account's current state" flag: any
  // photo/story/music write, the post-action background sync, and the very first
  // snapshot read. Drives the content-body overlay and disables the media
  // controls. Excludes the text Save (footer has its own spinner).
  //
  // `isPending` is in it because an empty list while the read is still in flight
  // renders as «Нет сохранённой музыки» — a definitive claim about a live
  // account, the same failure the error branch above already guards against.
  const busy =
    syncing ||
    // `&& !loadError`: a cancelled first read that then failed leaves the query
    // pending for good (nothing re-fetches on its own), and the scrim sits ON TOP
    // of the error banner — including its retry button. With a reason on screen
    // the empty tabs are already explained, so the scrim has nothing to add.
    (snapshot.isPending && !loadError) ||
    Boolean(photoProgress) ||
    setMainPhoto.isPending ||
    removePhoto.isPending ||
    removeStory.isPending ||
    setStoryPinned.isPending ||
    addMusic.isPending ||
    removeMusic.isPending;

  // `form.reset(saved)` alone does not survive. form-core moves the form's own
  // `options.defaultValues` to the values it was reset with, so the next render
  // passes defaults that DEEP-DIFFER from them — and `FormApi.update` re-applies
  // `defaultValues` whenever they differ and the form is untouched, which a reset
  // has just made it. An inline object built from `account` therefore restores
  // the pre-save values one render later. Moving the baseline in step keeps the
  // comparison equal, so the re-apply becomes a no-op instead of a revert.
  const baseline = useRef({
    first_name: account.first_name ?? '',
    last_name: account.last_name ?? '',
    username: account.username ?? '',
    bio: account.bio ?? '',
  });
  const form = useForm({
    defaultValues: baseline.current,
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
            // Remember what the bio write carried: `updateProfile` answers with
            // a `User`, which has no `about`, so ok does not prove the bio
            // landed and only the live re-pull below can tell.
            rememberSavedBio(value.bio.trim());
            // Reset the baseline to the just-saved values so the form is no
            // longer "dirty" — otherwise closing afterwards wrongly prompts
            // "discard unsaved edits?" even though everything was saved. Both
            // halves are needed: `reset` clears the dirty meta, the baseline
            // write stops the next render from restoring the pre-save values.
            baseline.current = { ...value };
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

  // Telegram can accept `updateProfile` and silently ignore `about` — young
  // accounts, typically a bio advertising a channel. The post-save live pull is
  // the only witness, so a snapshot still reporting something else means the
  // text did not land. Derived, not state: every later pull re-evaluates it, so
  // a replication lag that briefly answers with the pre-write value clears
  // itself instead of leaving a permanent false alarm. Gated on the pull having
  // settled and on it having succeeded — a stale or failed read is not evidence.
  const bioDropped =
    savedBio !== null && !syncing && !loadError && (snapshot.data?.bio ?? '') !== savedBio;

  // A rejected save carries a stable code in the error envelope; username/bio
  // codes render under their field, the rest beside the footer's Save button
  // (which is global — the box must not be trapped inside the text tab).
  // Unknown codes show as-is (plus the global mutation toast — same contract
  // as channels).
  const saveError = updateProfile.isError ? updateProfile.error : null;
  const saveErrorField = saveError ? profileErrorField(saveError) : null;
  const saveErrorText = saveError
    ? profileErrorText(saveError, t, t('accounts.profile.saveError'))
    : null;
  // A stale server error must not outlive the edit that addresses it: while an
  // error is shown, the first form-value change clears it. Store subscription,
  // not a values selector — typing must not re-render the whole modal.
  //
  // A programmatic re-seed is NOT such an edit, and it is indistinguishable from
  // one by the values reference alone: `seedField` writes through
  // `setFieldValue`, which builds a new `values` object even for an identical
  // value. So «Обновить» — pressed to check the account the save error is about —
  // erased the error with nothing fixed: the flood_wait duration telling the
  // operator how long to wait, or the "username taken" verdict next to a handle
  // the same pull had just re-seeded to Telegram's own. `seeding` swallows the
  // notification the seed raises, and moves the baseline with it so the next
  // unrelated store event (a blur, a validation settling) doesn't clear it
  // either. The user's own keystroke still does — that is the point of the effect.
  const seeding = useRef(false);
  const errorBaseline = useRef<unknown>(null);
  const showingSaveError = updateProfile.isError;
  const resetSaveError = updateProfile.reset;
  useEffect(() => {
    if (!showingSaveError) return;
    errorBaseline.current = form.store.state.values;
    // ``subscribe`` hands back a Subscription, not the bare unsubscribe function
    // (@tanstack/store, since react-form 1.x), so the effect cleans up through it.
    const subscription = form.store.subscribe(() => {
      if (seeding.current) {
        errorBaseline.current = form.store.state.values;
        return;
      }
      if (form.store.state.values !== errorBaseline.current) resetSaveError();
    });
    return () => subscription.unsubscribe();
  }, [showingSaveError, resetSaveError, form]);

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
  //
  // See `seedField` above for why each write also refreshes the field's validation
  // state rather than only its value.
  // Seeds one field. `dontUpdateMeta` keeps the form clean, but it also leaves the
  // onMount verdict untouched — and that verdict judged the STORED value this write
  // just replaced. Left stale, an account whose row has no first name but whose
  // snapshot does shows «Укажите имя» under a field displaying the real name, with
  // Save dead for good: `canSubmit` counts onMount errors, and no later edit to any
  // field clears another field's. So drop it and re-validate through onChange, which
  // does reflect the new value — dropping it alone would enable Save on a seeded
  // value that is itself invalid. Neither step marks the form dirty.
  const seedField = useCallback(
    (name: 'first_name' | 'last_name' | 'username' | 'bio', value: string) => {
      // Flagged for the save-error subscription above: these writes are the
      // dashboard's, not the operator's, so they must not count as addressing
      // the error. Cleared in `finally` — a throwing validator must not leave
      // the guard latched, which would make the real next edit a no-op.
      seeding.current = true;
      try {
        form.setFieldValue(name, value, { dontUpdateMeta: true });
        form.setFieldMeta(name, (meta) => ({
          ...meta,
          errorMap: { ...meta.errorMap, onMount: undefined },
        }));
        form.validateField(name, 'change');
      } finally {
        seeding.current = false;
      }
    },
    [form],
  );

  const seedForm = useCallback(
    (view: AccountProfileView) => {
      if (view.error) return;
      if (view.first_name != null) {
        seedField('first_name', view.first_name);
      }
      seedField('last_name', view.last_name ?? '');
      seedField('username', view.username ?? '');
      // Hold the bio while a save is outstanding. Seeding it hands the operator
      // back the text Telegram refused, and the next save — dirty through any
      // other field — pushes that old text while the warning vanishes as though
      // it had resolved. Their own text stays; the warning says why Telegram
      // lacks it. No `=== savedBio` half: when the two agree this seed writes
      // the value already in the field, so it would never be observable. The
      // hold is released by editing the field (`rememberSavedBio(null)`), which
      // is also what lets «Обновить» pull the bio back down.
      if (savedBioRef.current === null) {
        seedField('bio', view.bio ?? '');
      }
      // ponytail: the same hold is NOT applied to the names or the username. A
      // save whose forced pull lags AND carries unrelated drift re-seeds them to
      // the pre-save values while the baseline holds the new ones, so a later
      // save dirtied elsewhere re-pushes the old name — the bio failure, minus
      // the warning that explains it. Left alone deliberately: `about` is the
      // only field Telegram is documented to ignore silently, so for the rest
      // this needs a real lag, and holding every field would freeze «Обновить»
      // after every save. Revisit if an operator reports a name reverting.
    },
    [seedField],
  );

  // The row snapshot the modal opened with can lag Telegram; once the live
  // profile arrives, re-seed the fields — but never clobber user edits.
  const snapshotData = snapshot.data;
  useEffect(() => {
    if (snapshotData && !form.state.isDirty) seedForm(snapshotData);
  }, [snapshotData, form, seedForm]);

  // The ✓/✗ flash timer, kept so a later press or an unmount can cancel it.
  const flashTimer = useRef<number | null>(null);
  useEffect(
    () => () => {
      if (flashTimer.current !== null) window.clearTimeout(flashTimer.current);
    },
    [],
  );

  // «Обновить»: force a live re-pull (bypasses the read cache), write it into the
  // rendered snapshot, and reseed the header + text fields from the fresh profile.
  const onRefresh = async () => {
    setRefreshState('loading');
    // Own the overlay for the whole pull, exactly as the post-action `refresh()`
    // does — `forcePull`'s latest-gen `finally` is what clears it. Without this
    // the button's only guard was `refreshState`, which the 1.4s timer below
    // clears: a ✓ armed at t≈50ms re-enabled the button at t=1450 while a second
    // press's pull was still in flight, and every press is a `refresh=true`
    // round trip that deliberately bypasses the server's 30s read cache — the
    // FLOOD_WAIT hazard the rest of this file is built to avoid.
    setSyncing(true);
    // The privacy tab runs its own query, outside the snapshot: without this,
    // «Обновить» resets the «Обновлено … назад» label while the levels on
    // screen stay stale — the control would be lying on that tab. Only when it
    // is the visible tab: three getPrivacy round trips per press from the text
    // or media tabs is spend for nothing.
    if (tab === 'privacy') {
      void queryClient.invalidateQueries({
        queryKey: accountPrivacyQueryKey({ path: { account_id: account.account_id } }),
      });
    }
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
    } catch (error) {
      setRefreshState('error');
      // Same as the fire-and-forget `refresh()` path: a refused pull must mark
      // the snapshot untrustworthy, not just flash a 1.4s ✗. Otherwise the
      // stale fields keep rendering as current — and the bio verdict, which
      // stays silent while `loadError` holds, would be recomputed from them.
      setSyncError(profileErrorText(error, t, t('accounts.profile.privacy.noReason')));
    } finally {
      // Held in a ref so a press supersedes the previous press's timer and the
      // unmount cleanup below cancels the last one: an orphan timer lands 'idle'
      // on a tree that is gone, or on a newer pull that has not finished.
      if (flashTimer.current !== null) window.clearTimeout(flashTimer.current);
      flashTimer.current = window.setTimeout(() => {
        setRefreshState('idle');
      }, 1400);
    }
  };

  // Escape / backdrop / × ask before discarding unsaved text edits.
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const requestClose = () => {
    // A photo batch outlives the modal: `uploadPhotos` is a sequential loop with
    // no abort path and react-query keeps the mutation in its cache, so closing
    // after file #2 hides the progress overlay while #3..#5 keep uploading — each
    // one becoming the account's avatar — and the post-batch refresh() is gone
    // with the tree that owned it. Same reason AddStoryModal locks its exits
    // mid-publish; the buttons below are disabled so this is not a dead click.
    if (photoProgress) return;
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
    // ONE avatar re-sync for the whole batch, never per file. /accounts/photo
    // takes one file per call, so the server-side re-sync it used to do spent a
    // get_me plus a thumb download on every upload and all but the last were
    // immediately superseded — working against the very FLOOD_WAIT budget this
    // sequential loop exists to protect. It is its own endpoint now, and this is
    // the only caller: the loop above is the sole frontend path to
    // /accounts/photo (a single-file pick runs the same loop with one entry).
    //
    // Called through the SDK rather than a useMutation on purpose: every
    // mutation rejection goes through MutationCache.onError, which toasts it
    // (shared/lib/query-client.ts), and the list avatar is cosmetic — a refused
    // re-sync must not raise an error over a batch that uploaded fine. Swallowed
    // like the per-file failures above, minus the toast they DO deserve. The row
    // then keeps its previous thumbnail until the next session check.
    //
    // Not applied to remove-photo or set-main: those keep their own server-side
    // re-sync (services/accounts/media.py), where one click re-syncs once and
    // nothing is superseded.
    try {
      await resyncAccountAvatar({ path: { account_id: account.account_id } });
    } catch {
      // cosmetic — deliberately silent, see above
    }
    // refresh()'s accounts-key invalidation is what pulls the new avatar_thumb
    // into the table, so the re-sync has to land BEFORE it.
    refresh();
  };

  const tabBtn = (value: Tab): string =>
    `shrink-0 whitespace-nowrap border-b-2 py-lg text-body font-medium transition-colors ${tab === value ? 'border-action-primary text-content-primary' : 'border-transparent text-content-muted'}`;

  // The other half of the ARIA tabs pattern (the roles landed with the tablist):
  // the tablist is ONE tab stop via roving tabindex, and Left/Right/Home/End move
  // between the tabs — otherwise a keyboard user Tabs through all six.
  const onTabKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    const step = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0;
    let next: Tab | undefined;
    if (step !== 0) next = TABS[(TABS.indexOf(tab) + step + TABS.length) % TABS.length];
    else if (event.key === 'Home') next = TABS[0];
    else if (event.key === 'End') next = TABS[TABS.length - 1];
    if (next === undefined) return;
    event.preventDefault();
    setTab(next);
    // Automatic activation: selection follows focus, so focus has to follow too.
    document.getElementById(`profile-tab-${next}`)?.focus();
  };

  const refreshLook = REFRESH_LOOK[refreshState === 'loading' ? 'idle' : refreshState];
  // An in-flight photo batch owns the modal: the exits are locked (see
  // requestClose) and must look it rather than silently ignoring the click.
  const uploading = Boolean(photoProgress);

  return (
    <>
      <Modal
        onClose={requestClose}
        className="w-panel"
        // A fixed name, unlike the visible heading below it — the same choice as
        // ChannelEditModal. `fullName` flips once the live snapshot lands, and again
        // after a rename saves, and an ARIA name change while a dialog is open is
        // never re-announced. Latching the row's name instead only moved the problem:
        // the container was then announced by phone while the heading read a
        // different name, and it went stale on a renamed row's new `account` prop.
        label={t('accounts.profile.dialog')}
      >
        <div className="flex max-h-dialog flex-col overflow-hidden">
          {/* header */}
          <div className="flex items-center gap-lg border-b border-line-row px-xl py-xl">
            {/* The two gradient stops are decorative and exist only to differ from each
                other behind an avatar that has not loaded — deliberately NOT tokens, for
                the same reason as the media tiles in `_profileShared`. */}
            <div
              // eslint-disable-next-line design-tokens/no-raw-values -- see the note above: two decorative stops, single-use by design
              className="flex size-face shrink-0 items-center justify-center overflow-hidden rounded-full bg-gradient-to-br from-[#7c9cff] to-[#a0e0c0] text-stat font-semibold text-on-inverse"
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
              {/* A heading, not a div: the dialog's own name is fixed (see above), so
                  this is the only place the account's identity is exposed, and heading
                  navigation is how a screen-reader user reaches it. */}
              <h2 className="truncate type-dialog-title">{fullName}</h2>
              <div className="truncate type-prose">
                {liveUser ? `@${liveUser} · ` : ''}
                {account.phone ?? account.account_id}
              </div>
            </div>
            <div className="flex shrink-0 flex-col items-end gap-tight">
              <button
                type="button"
                disabled={refreshState === 'loading' || syncing}
                onClick={() => {
                  void onRefresh();
                }}
                className={`inline-flex items-center gap-sm rounded-full border bg-surface-card px-md py-tight text-body font-medium transition-colors disabled:opacity-70 ${refreshLook.border}`}
              >
                <span
                  className={`inline-flex ${
                    refreshState === 'loading'
                      ? 'tb-spin'
                      : refreshState === 'idle'
                        ? ''
                        : 'tb-swapin'
                  }`}
                >
                  <svg
                    width="13"
                    height="13"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth={refreshLook.stroke}
                  >
                    <path d={refreshLook.path} />
                  </svg>
                </span>
                {t(refreshLook.labelKey)}
              </button>
              <SyncLabel updatedAt={snapshot.dataUpdatedAt} />
            </div>
            <IconButton
              size="md"
              onClick={requestClose}
              disabled={uploading}
              aria-label={t('accounts.profile.close')}
              className="ml-hair text-title"
            >
              ×
            </IconButton>
          </div>

          {/* tabs — a real tablist: the active tab was conveyed by colour and a
              bottom border only, so a screen reader announced six plain buttons
              with no way to tell which one is showing. */}
          {/* Not `shared/ui`'s `SegmentedControl`: that one is a radiogroup, and these
              six really do switch a panel — they own the `aria-controls`/`tabpanel`
              pair below. They are also the app's only underline tab strip, with no
              tray and no filled option, so there is nothing here for a fill variant to
              carry. One wearer, hand-written. */}
          {/* Six labels overflow a phone-width modal; scroll them rather than wrap,
              so the roving-tabindex row stays a single line. */}
          <div
            role="tablist"
            className="tb-scroll flex gap-xl overflow-x-auto border-b border-line-row px-xl"
          >
            {TABS.map((value) => (
              <button
                key={value}
                type="button"
                role="tab"
                id={`profile-tab-${value}`}
                aria-selected={tab === value}
                aria-controls="profile-tabpanel"
                tabIndex={tab === value ? 0 : -1}
                onKeyDown={onTabKeyDown}
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
          <div
            role="tabpanel"
            id="profile-tabpanel"
            aria-labelledby={`profile-tab-${tab}`}
            className="tb-scroll relative flex-1 overflow-y-auto p-xl"
          >
            {/* Applying overlay: every media edit calls refresh(), which re-pulls
                the snapshot from Telegram in the background. A greyed scrim with a
                spinner signals "still working" and blocks input to stop double-
                submits. It sits inside the overflow container, so `inset-0` pins it
                to the visible viewport rather than scrolling away. The text tab is
                excluded — its Save keeps the footer's own spinner/✓ — and so is
                the channels and privacy tabs, which run on their own queries. */}
            {busy && tab !== 'text' && tab !== 'channels' && tab !== 'privacy' && (
              <div
                role="status"
                aria-live="polite"
                aria-label={t('accounts.profile.syncing')}
                className="absolute inset-0 z-raised flex flex-col items-center justify-center gap-md bg-black/10 tb-ovfade"
              >
                {/* `line-strong`, not the default line: this ring sits on the modal's own
                    `bg-black/10` scrim, which composites within a unit of `line` — the
                    unlit half disappeared into it and left a bare blue arc. */}
                <span className="tb-spin inline-block size-tile rounded-full border-[3px] border-line-strong border-t-action-primary" />
                <span className="type-label">
                  {photoProgress
                    ? t('accounts.profile.uploadingCount', photoProgress)
                    : t('accounts.profile.syncing')}
                </span>
              </div>
            )}
            {loadError && tab !== 'channels' && tab !== 'privacy' && (
              <Notice tone="danger" className="mb-lg flex items-center justify-between gap-md">
                <span>{t('accounts.profile.loadError', { reason: loadErrorReason })}</span>
                <Button
                  size="xs"
                  variant="danger"
                  className="bg-surface-card"
                  disabled={refreshState === 'loading' || syncing}
                  onClick={() => {
                    void onRefresh();
                  }}
                >
                  {t('accounts.profile.refresh')}
                </Button>
              </Notice>
            )}
            {tab === 'text' && (
              <div className="flex flex-col gap-lg">
                <div className="grid grid-cols-1 md:grid-cols-2 gap-md">
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
                        <span className="absolute left-lg text-body text-content-subtle">@</span>
                        <Input
                          className="pl-page"
                          value={field.state.value}
                          onChange={(event) => {
                            field.handleChange(event.target.value);
                          }}
                          onBlur={field.handleBlur}
                        />
                      </div>
                      {saveErrorField === 'username' && saveErrorText != null && (
                        <span
                          role="alert"
                          className="mt-tight block type-caption font-medium text-danger"
                        >
                          {saveErrorText}
                        </span>
                      )}
                    </FormField>
                  )}
                </form.Field>
                <form.Field name="bio">
                  {(field) => (
                    <FormField field={field} label={t('accounts.profile.bio')}>
                      <Textarea
                        className="resize-none [font-family:inherit]"
                        data-testid="profile-bio"
                        rows={3}
                        value={field.state.value}
                        onChange={(event) => {
                          // A new edit supersedes the verdict on the last save.
                          // Guarded: `rememberSavedBio` setStates, and this is the
                          // one field not isolated behind its own `form.Field`
                          // subscription, so unguarded it re-rendered the whole
                          // modal on every keystroke — which the save-error
                          // subscription and `canSubmit`'s useStore exist to avoid.
                          if (savedBioRef.current !== null) rememberSavedBio(null);
                          field.handleChange(event.target.value);
                        }}
                        onBlur={field.handleBlur}
                      />
                      {saveErrorField === 'bio' && saveErrorText != null && (
                        <span
                          role="alert"
                          className="mt-tight block type-caption font-medium text-danger"
                        >
                          {saveErrorText}
                        </span>
                      )}
                      {bioDropped && (
                        <span
                          role="alert"
                          data-testid="bio-not-applied"
                          className="mt-tight block type-caption font-medium text-warning-deep"
                        >
                          {t('accounts.profile.bioNotApplied')}
                        </span>
                      )}
                    </FormField>
                  )}
                </form.Field>
              </div>
            )}

            {tab === 'photo' && (
              <PhotoTab
                photos={photos}
                busy={busy}
                uploading={uploading}
                onUpload={(files) => {
                  void uploadPhotos(files);
                }}
                onRemove={(photo) => {
                  setConfirm({
                    kind: 'Photo',
                    run: () =>
                      removePhoto
                        .mutateAsync({
                          path: { account_id: account.account_id },
                          body: {
                            photo_id: photo.photo_id,
                            access_hash: photo.access_hash,
                            file_reference: photo.file_reference,
                          },
                        })
                        .finally(refresh),
                  });
                }}
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
                onRemove={(story) => {
                  setConfirm({
                    kind: 'Story',
                    run: () =>
                      removeStory
                        .mutateAsync({
                          path: { account_id: account.account_id },
                          body: { story_id: story.story_id },
                        })
                        .finally(refresh),
                  });
                }}
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
                  const body: MusicRemoveRequest = {
                    file_id: track.file_id,
                    access_hash: track.access_hash ?? '0',
                    file_reference: track.file_reference,
                  };
                  setConfirm({
                    kind: 'Music',
                    run: () =>
                      removeMusic
                        .mutateAsync({ path: { account_id: account.account_id }, body })
                        .finally(refresh),
                  });
                }}
              />
            )}

            {tab === 'channels' && <ChannelsTab accountId={account.account_id} />}

            {tab === 'privacy' && <PrivacyTab accountId={account.account_id} />}
          </div>

          {/* footer */}
          <div className="flex items-center justify-end gap-sm border-t border-line-row px-xl py-lg">
            {/* Non-field save errors (account_frozen, flood_wait, unknown)
                live beside the global Save button, visible from any tab. */}
            {saveErrorField === null && saveErrorText != null && (
              <div
                role="alert"
                title={saveErrorText}
                className="mr-auto min-w-0 truncate type-label text-danger"
              >
                {saveErrorText}
              </div>
            )}
            <Button onClick={requestClose} disabled={uploading}>
              {t('accounts.profile.cancel')}
            </Button>
            <Button
              variant="primary"
              onClick={() => {
                void form.handleSubmit();
              }}
              disabled={updateProfile.isPending || !canSave || !isDirty}
              className={saved ? 'bg-success-deep hover:bg-success-deep' : ''}
            >
              {updateProfile.isPending ? (
                <span className="inline-flex items-center gap-sm">
                  <span className="tb-spin inline-block size-spinner rounded-full border-2 border-white/40 border-t-white" />
                  {t('accounts.profile.saving')}
                </span>
              ) : saved ? (
                <span className="inline-flex items-center gap-sm">
                  <span className="tb-swapin inline-flex">
                    <Icon name="check" size={16} />
                  </span>
                  <span className="tb-swapin inline-block" style={{ animationDelay: '0.09s' }}>
                    {t('accounts.profile.saved')}
                  </span>
                </span>
              ) : (
                t('accounts.profile.save')
              )}
            </Button>
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
      {confirm ? (
        <ConfirmModal
          title={t(`accounts.profile.remove${confirm.kind}Title`)}
          body={t(`accounts.profile.remove${confirm.kind}Body`)}
          confirmLabel={t(`accounts.profile.remove${confirm.kind}Confirm`)}
          cancelLabel={t('accounts.profile.cancel')}
          onClose={() => {
            setConfirm(null);
          }}
          onConfirm={confirm.run}
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
