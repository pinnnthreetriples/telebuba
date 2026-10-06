import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  accountChannelsQueryOptions,
  accountDisplayName,
  AccountAvatar,
  accountPrivacyQueryKey,
  accountProfileSnapshotQueryKey,
  accountScheduledPostsQueryKey,
  addAccountMusicMutation,
  allAccountsQueryOptions,
  BulkAccountPicker,
  createAccountChannelMutation,
  invalidateAccountViews,
  postAccountStoryMutation,
  publishAccountChannelPostMutation,
  scheduleAccountPhotoMutation,
  scheduleAccountStoryMutation,
  setAccountChannelPhotoMutation,
  setAccountPhotoMutation,
  setAccountPrivacyMutation,
  updateAccountProfileMutation,
  uploadScheduledMediaMutation,
} from '@/entities/account';
import {
  BULK_MIN_LEAD_MS,
  bulkRunAts,
  bulkTailMs,
  clampToLead,
  defaultRunAt,
  MAX_LEAD_MS,
  newBatchId,
  runAtProblem,
  toIso,
  useNow,
  type ScheduleMode,
} from '@/features/schedule-post';
import { resyncAccountAvatar } from '@/shared/api';
import type { AccountRead } from '@/shared/api';
import { formatLocalDateTime } from '@/shared/lib';
import { Button, CloseButton, Icon, IconButton, Modal, TabList } from '@/shared/ui';

import {
  BulkChannelsTab,
  USERNAME_SLOT,
  type ChannelDraft,
  type PostDraft,
} from './BulkChannelsTab';
import { BulkMusicTab, BulkPhotoTab, BulkSchedulePanel, BulkStoriesTab } from './BulkMediaTabs';
import { BulkPrivacyTab } from './BulkPrivacyTab';
import { BulkProgress } from './BulkProgress';
import { BulkTextTab } from './BulkTextTab';
import {
  CHANNEL_USERNAME_RE,
  errorChannelId,
  postTextMax,
  VIDEO_SUFFIXES,
} from './_channelsShared';
import {
  PRIVACY_KEYS,
  TEXT_FIELDS,
  TEXT_MAX,
  type PrivacyKey,
  type PrivacyLevel,
  type TextFieldKey,
} from './_profileShared';
import { useBulkRun } from './useBulkRun';
import { useMountedRef } from './useMountedRef';

type Tab = 'text' | 'photo' | 'stories' | 'music' | 'channels' | 'privacy';
const TABS = [
  'text',
  'photo',
  'stories',
  'music',
  'channels',
  'privacy',
] as const satisfies readonly Tab[];

type Audience = 'contacts' | 'close_friends' | 'public';

// Photos and stories may wait for a time; the other tabs always apply on the click.
const SCHEDULABLE: ReadonlySet<Tab> = new Set(['photo', 'stories']);
const DEFAULT_SPREAD_MINUTES = 10;

const EMPTY_CHANNEL: ChannelDraft = {
  avatar: null,
  title: '',
  about: '',
  isPublic: false,
  username: '',
  reactionsOff: false,
};

// The profile editor's bulk twin: the same edit written to many accounts at once,
// one account at a time.
//
// Every tab is a different thing to apply, so each owns its own "is there
// anything to apply" and its own per-account step; the shell — batch strip, tab
// strip, run rows, footer — is the same for all of them.
export function BulkEditModal({ account, onClose }: { account: AccountRead; onClose: () => void }) {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const now = useNow();
  const upload = useMutation(uploadScheduledMediaMutation());
  const schedulePhoto = useMutation(scheduleAccountPhotoMutation());
  const scheduleStory = useMutation(scheduleAccountStoryMutation());
  const updateProfile = useMutation(updateAccountProfileMutation());
  const setPhoto = useMutation(setAccountPhotoMutation());
  const postStory = useMutation(postAccountStoryMutation());
  const addMusic = useMutation(addAccountMusicMutation());
  const setPrivacy = useMutation(setAccountPrivacyMutation());
  const createChannel = useMutation(createAccountChannelMutation());
  const setChannelPhoto = useMutation(setAccountChannelPhotoMutation());
  const publishPost = useMutation(publishAccountChannelPostMutation());

  const [ids, setIds] = useState<string[]>([account.account_id]);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [tab, setTab] = useState<Tab>('text');
  const [started, setStarted] = useState(false);

  const [on, setOn] = useState<Record<TextFieldKey, boolean>>({
    first_name: false,
    last_name: false,
    bio: false,
  });
  const [value, setValue] = useState<Record<TextFieldKey, string>>({
    first_name: '',
    last_name: '',
    bio: '',
  });
  const [photos, setPhotos] = useState<File[]>([]);
  const [perAccount, setPerAccount] = useState(false);
  const [storyFiles, setStoryFiles] = useState<File[]>([]);
  const [caption, setCaption] = useState('');
  const [audience, setAudience] = useState<Audience>('contacts');
  const [track, setTrack] = useState<File | null>(null);
  const [levels, setLevels] = useState<Partial<Record<PrivacyKey, PrivacyLevel>>>({});
  const [channelMode, setChannelMode] = useState<'create' | 'post'>('create');
  const [channel, setChannel] = useState<ChannelDraft>(EMPTY_CHANNEL);
  const [post, setPost] = useState<PostDraft>({ text: '', file: null });

  const [scheduleMode, setScheduleMode] = useState<ScheduleMode>('now');
  const [baseRunAt, setBaseRunAt] = useState<number | null>(() =>
    defaultRunAt(Date.now(), 2 * BULK_MIN_LEAD_MS),
  );
  const [spreadMinutes, setSpreadMinutes] = useState(DEFAULT_SPREAD_MINUTES);
  const [preparing, setPreparing] = useState(false);
  // When each account's post will go out, for its row once it is scheduled.
  const scheduledAt = useRef<Record<string, number>>({});
  // A close during the upload must not let the batch start afterwards with no UI.
  const alive = useMountedRef();

  const bulk = useBulkRun();

  // The fleet is already in cache behind the picker; this is the same key, so the
  // chips get names without a second request.
  const fleet = useQuery(allAccountsQueryOptions());
  const byId = new Map((fleet.data?.items ?? []).map((row) => [row.account_id, row]));
  const label = (accountId: string) => {
    const row = byId.get(accountId);
    return row ? accountDisplayName(row) : accountId;
  };
  const picked = ids.map((accountId) => byId.get(accountId) ?? { account_id: accountId });

  const ticked = TEXT_FIELDS.filter((key) => on[key]);
  const textReady =
    ticked.length > 0 &&
    !ticked.some((key) => value[key].trim().length > TEXT_MAX[key]) &&
    !(on.first_name && value.first_name.trim() === '');

  // A public bulk create needs `{n}` in the handle for anything past the first
  // account: Telegram handles are unique, so the same one twice is a refusal by
  // construction.
  const handle = channel.username.trim();
  // Checked at BOTH ends of the batch, not just at `1`: the number grows a digit
  // at the tenth account, and a handle exactly 32 chars long with `1` is 33 with
  // `10` — refused for accounts 10..N only, after nine had already been created.
  const handleReady =
    !channel.isPublic ||
    ([1, ids.length].every((n) =>
      CHANNEL_USERNAME_RE.test(handle.replace(USERNAME_SLOT, String(n))),
    ) &&
      (ids.length === 1 || handle.includes(USERNAME_SLOT)));
  const READY: Record<Tab, boolean> = {
    text: textReady,
    photo: photos.length > 0,
    stories: storyFiles.length > 0,
    music: track !== null,
    channels:
      channelMode === 'create'
        ? channel.title.trim() !== '' && handleReady
        : (post.text.trim() !== '' || post.file !== null) &&
          post.text.length <= postTextMax(post.file),
    privacy: Object.keys(levels).length > 0,
  };
  const NOTE: Record<Tab, string> = {
    text: t('accounts.bulk.fieldCount', { done: ticked.length, total: TEXT_FIELDS.length }),
    photo: perAccount
      ? t('accounts.bulk.notePhotoEach', { n: photos.length })
      : t('accounts.bulk.notePhotoOne'),
    stories: t('accounts.bulk.noteStory'),
    music: t('accounts.bulk.noteMusic'),
    channels:
      channelMode === 'create'
        ? t('accounts.bulk.noteChannelCreate', { count: ids.length })
        : t('accounts.bulk.noteChannelPost'),
    privacy: t('accounts.bulk.noteRows', {
      done: Object.keys(levels).length,
      total: PRIVACY_KEYS.length,
    }),
  };

  const step = async (accountId: string, index: number) => {
    if (tab === 'text') {
      const body = Object.fromEntries(ticked.map((key) => [key, value[key].trim()]));
      await updateProfile.mutateAsync({ body: { ...body, account_id: accountId } });
      return;
    }
    if (tab === 'photo') {
      // Cycled, not clamped: a set shorter than the batch keeps handing out
      // files instead of leaving the tail without a photo.
      const file = perAccount ? photos[index % photos.length] : photos[0];
      if (!file) return;
      await setPhoto.mutateAsync({ body: { account_id: accountId, file } });
      // Cosmetic and deliberately silent, exactly as in the single-account
      // upload: a refused re-sync must not fail an upload that landed.
      try {
        await resyncAccountAvatar({ path: { account_id: accountId } });
      } catch {
        // the row keeps its previous thumbnail until the next session check
      }
      return;
    }
    if (tab === 'stories') {
      const video = storyFiles.some((file) =>
        VIDEO_SUFFIXES.some((suffix) => file.name.toLowerCase().endsWith(suffix)),
      );
      await postStory.mutateAsync({
        path: { account_id: accountId },
        body: {
          files: storyFiles,
          media_kind: video ? 'video' : 'image',
          caption: caption.trim(),
          privacy_preset: audience,
          protect_content: false,
          collage_layout: null,
        },
      });
      return;
    }
    if (tab === 'music') {
      if (track)
        await addMusic.mutateAsync({ path: { account_id: accountId }, body: { file: track } });
      return;
    }
    if (tab === 'privacy') {
      await setPrivacy.mutateAsync({ path: { account_id: accountId }, body: levels });
      return;
    }
    if (channelMode === 'create') {
      let channelId: string | null = null;
      try {
        const result = await createChannel.mutateAsync({
          path: { account_id: accountId },
          body: {
            title: channel.title.trim(),
            about: channel.about.trim(),
            // `{n}` is the account's position, so each channel gets its own handle.
            username: channel.isPublic ? handle.replace(USERNAME_SLOT, String(index + 1)) : null,
            reactions_enabled: !channel.reactionsOff,
          },
        });
        channelId = result.channel_id ?? null;
      } catch (error) {
        // The channel can EXIST after a refusal: the gateway sends the username
        // after creating it, so an occupied handle leaves a private channel behind
        // and rides its id out on the error. Give that channel its avatar anyway,
        // then re-raise so the row still reports the refusal.
        const orphan = errorChannelId(error);
        if (orphan != null && channel.avatar) {
          await setChannelPhoto.mutateAsync({
            path: { account_id: accountId, channel_id: orphan },
            body: { file: channel.avatar },
          });
        }
        throw error;
      }
      if (channel.avatar && channelId != null) {
        await setChannelPhoto.mutateAsync({
          path: { account_id: accountId, channel_id: channelId },
          body: { file: channel.avatar },
        });
      }
      return;
    }
    // Post mode: this account's own channels, read fresh — the list decides how
    // many posts its turn is, and a stale one would skip a channel made since.
    const channels = await queryClient.fetchQuery(
      accountChannelsQueryOptions({ path: { account_id: accountId } }),
    );
    // Nothing to post into is not "done": a green row would claim this account
    // got the post, and the operator would never learn it has no channels.
    if (channels.items.length === 0) throw new Error(t('accounts.bulk.noChannels'));
    for (const item of channels.items) {
      await publishPost.mutateAsync({
        path: { account_id: accountId, channel_id: item.channel_id },
        body: { text: post.text.trim(), ...(post.file ? { file: post.file } : {}) },
      });
    }
  };

  const running =
    preparing || bulk.rows.some((row) => row.state === 'queued' || row.state === 'running');
  const later = SCHEDULABLE.has(tab) && scheduleMode === 'later';
  const tailTooFar =
    baseRunAt !== null && bulkTailMs(baseRunAt, ids.length, spreadMinutes) > now + MAX_LEAD_MS;
  const timeReady =
    !later || (runAtProblem(baseRunAt, now, BULK_MIN_LEAD_MS) === null && !tailTooFar);
  // Everything the batch reads is frozen once its upload starts.
  const locked = started || preparing;

  const invalidateBatch = () => {
    // Names, avatars and media of every account in the batch just changed; the
    // table behind this dialog — and any open profile snapshot — is showing
    // what they were.
    invalidateAccountViews(queryClient);
    for (const accountId of ids) {
      const path = { path: { account_id: accountId } };
      void queryClient.invalidateQueries({ queryKey: accountProfileSnapshotQueryKey(path) });
      void queryClient.invalidateQueries({ queryKey: accountPrivacyQueryKey(path) });
      void queryClient.invalidateQueries({
        queryKey: accountChannelsQueryOptions(path).queryKey,
      });
      void queryClient.invalidateQueries({ queryKey: accountScheduledPostsQueryKey(path) });
    }
  };

  // The files go over the wire ONCE, then every account gets a small JSON request
  // naming them — a batch of fifty is fifty tiny calls, not fifty uploads.
  const applyScheduled = async () => {
    if (baseRunAt === null) return;
    const files =
      tab === 'photo' ? (perAccount ? photos : photos.slice(0, 1)) : storyFiles.slice(0, 1);
    if (files.length === 0) return;
    setPreparing(true);
    const mediaIds: string[] = [];
    try {
      for (const file of files) {
        mediaIds.push((await upload.mutateAsync({ body: { file } })).media_id);
      }
    } catch {
      // The global mutation toast says why; nothing was scheduled yet.
      setPreparing(false);
      return;
    }
    if (!alive.current) return;
    setPreparing(false);
    const plan = bulkRunAts(baseRunAt, ids.length, spreadMinutes, Math.random);
    const batchId = newBatchId();
    scheduledAt.current = {};
    setStarted(true);
    void bulk
      .run(ids, async (accountId, index) => {
        // Re-checked at send time: a long batch can outlive the lead of its tail.
        const runAt = clampToLead(plan[index] ?? baseRunAt, Date.now());
        const pick = index % mediaIds.length;
        const shared = {
          run_at: toIso(runAt),
          filename: files[pick]?.name ?? null,
          batch_id: batchId,
          client_key: `row${String(index)}`,
        };
        const path = { account_id: accountId };
        if (tab === 'photo') {
          await schedulePhoto.mutateAsync({
            path,
            body: { ...shared, media_id: mediaIds[pick] ?? '' },
          });
        } else {
          await scheduleStory.mutateAsync({
            path,
            body: {
              ...shared,
              media_ids: mediaIds.slice(0, 1),
              caption: caption.trim() || null,
              privacy_preset: audience,
              protect_content: false,
              collage_layout: null,
            },
          });
        }
        scheduledAt.current[accountId] = runAt;
      })
      .finally(invalidateBatch);
  };

  const apply = () => {
    if (later) {
      void applyScheduled();
      return;
    }
    setStarted(true);
    void bulk.run(ids, step).finally(invalidateBatch);
  };

  return (
    <>
      <Modal
        onClose={running ? () => undefined : onClose}
        size="panel"
        label={t('accounts.bulk.title')}
      >
        <div className="flex h-profileDialog max-h-dialog flex-col overflow-hidden">
          <div className="flex items-center gap-lg border-b border-line-row px-xl py-xl">
            <div className="flex size-face shrink-0 items-center justify-center rounded-full bg-info-tint text-info-strong">
              <Icon name="users" size={20} />
            </div>
            <div className="min-w-0 flex-1">
              <h2 className="type-dialog-title">{t('accounts.bulk.title')}</h2>
              <div className="truncate type-prose">
                {t('accounts.bulk.selected', { count: ids.length })}
              </div>
            </div>
            <CloseButton
              onClick={onClose}
              disabled={running}
              aria-label={t('accounts.profile.close')}
            />
          </div>

          <div className="flex items-center gap-md border-b border-line-row px-xl py-md">
            <IconButton
              size="sm"
              disabled={locked}
              onClick={() => {
                setPickerOpen(true);
              }}
              aria-label={t('accounts.bulk.add')}
            >
              <Icon name="plus" size={16} />
            </IconButton>
            <div className="tb-scroll flex flex-1 items-center gap-sm overflow-x-auto py-hair">
              {picked.map((row) => (
                <span key={row.account_id} className="group relative shrink-0">
                  <AccountAvatar
                    account={row}
                    className="size-tile rounded-full"
                    fallbackClassName="bg-canvas text-content-muted type-label"
                  />
                  {!locked && ids.length > 1 && (
                    <IconButton
                      size="sm"
                      shape="circle"
                      aria-label={t('accounts.bulk.remove', { name: label(row.account_id) })}
                      onClick={() => {
                        setIds((prev) => prev.filter((id) => id !== row.account_id));
                      }}
                      className="absolute -right-hair -top-hair bg-surface-card opacity-0 transition-opacity focus-visible:opacity-100 group-hover:opacity-100"
                    >
                      <Icon name="close" size={16} />
                    </IconButton>
                  )}
                </span>
              ))}
            </div>
          </div>

          {/* Disabled while the batch's files upload: the batch has already read
              the tab, the files and the time, so a change here would be ignored. */}
          <fieldset disabled={preparing} className="contents">
            {!started && (
              <TabList
                options={TABS.map((value) => ({
                  value,
                  label: t(`accounts.profile.tab.${value}`),
                }))}
                value={tab}
                onChange={setTab}
                idPrefix="bulk-tab"
                panelId="bulk-tabpanel"
                ariaLabel={t('accounts.bulk.title')}
              />
            )}

            <div
              role={started ? undefined : 'tabpanel'}
              id={started ? undefined : 'bulk-tabpanel'}
              aria-labelledby={started ? undefined : `bulk-tab-${tab}`}
              className="tb-scroll flex flex-1 flex-col gap-lg overflow-y-auto p-xl"
            >
              {started ? (
                <BulkProgress
                  rows={bulk.rows}
                  label={label}
                  okLabel={(accountId) => {
                    const at = scheduledAt.current[accountId];
                    return at === undefined
                      ? t('accounts.bulk.rowOk')
                      : t('accounts.schedule.bulkRowOk', {
                          when: formatLocalDateTime(at, i18n.language),
                        });
                  }}
                />
              ) : tab === 'text' ? (
                <BulkTextTab
                  on={on}
                  value={value}
                  onToggle={(key) => {
                    setOn((prev) => ({ ...prev, [key]: !prev[key] }));
                  }}
                  onValue={(key, next) => {
                    setValue((prev) => ({ ...prev, [key]: next }));
                  }}
                />
              ) : tab === 'photo' ? (
                <BulkPhotoTab
                  files={photos}
                  spread={perAccount}
                  onFiles={setPhotos}
                  onSpread={setPerAccount}
                />
              ) : tab === 'stories' ? (
                <BulkStoriesTab
                  files={storyFiles}
                  caption={caption}
                  audience={audience}
                  onFiles={setStoryFiles}
                  onCaption={setCaption}
                  onAudience={setAudience}
                />
              ) : tab === 'music' ? (
                <BulkMusicTab file={track} onFile={setTrack} />
              ) : tab === 'channels' ? (
                <BulkChannelsTab
                  mode={channelMode}
                  channel={channel}
                  post={post}
                  onMode={setChannelMode}
                  onChannel={setChannel}
                  onPost={setPost}
                />
              ) : (
                <BulkPrivacyTab
                  levels={levels}
                  onPick={(key, level) => {
                    setLevels((prev) => {
                      const next = { ...prev };
                      if (level === null) delete next[key];
                      else next[key] = level;
                      return next;
                    });
                  }}
                />
              )}
              {!started && SCHEDULABLE.has(tab) && (
                <BulkSchedulePanel
                  mode={scheduleMode}
                  onMode={setScheduleMode}
                  base={baseRunAt}
                  onBase={setBaseRunAt}
                  spread={spreadMinutes}
                  onSpread={setSpreadMinutes}
                  now={now}
                  tailTooFar={tailTooFar}
                />
              )}
              {!started && <div className="type-caption sm:hidden">{NOTE[tab]}</div>}
            </div>
          </fieldset>

          <div className="flex items-center justify-end gap-sm border-t border-line-row px-xl py-lg">
            {!started && <div className="mr-auto hidden type-label sm:block">{NOTE[tab]}</div>}
            {started ? (
              running ? (
                <Button variant="danger" onClick={bulk.stop}>
                  {t('accounts.bulk.stop')}
                </Button>
              ) : (
                <Button variant="primary" onClick={onClose}>
                  {t('accounts.bulk.done')}
                </Button>
              )
            ) : (
              <>
                <Button onClick={onClose} disabled={preparing} className="px-md sm:px-2xl">
                  {t('accounts.profile.cancel')}
                </Button>
                <Button
                  variant="primary"
                  className="px-md sm:px-2xl"
                  disabled={ids.length === 0 || !READY[tab] || !timeReady || preparing}
                  loading={preparing}
                  onClick={apply}
                >
                  {later
                    ? t('accounts.schedule.bulkApply', { count: ids.length })
                    : t('accounts.bulk.apply', { count: ids.length })}
                </Button>
              </>
            )}
          </div>
        </div>
      </Modal>
      {pickerOpen && (
        <BulkAccountPicker
          selected={ids}
          onApply={(next) => {
            setIds(next);
            setPickerOpen(false);
          }}
          onClose={() => {
            setPickerOpen(false);
          }}
        />
      )}
    </>
  );
}
