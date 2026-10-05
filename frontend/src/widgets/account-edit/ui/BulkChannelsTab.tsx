import { useTranslation } from 'react-i18next';

import {
  Button,
  Icon,
  IconButton,
  Input,
  SegmentedControl,
  Textarea,
  toastError,
} from '@/shared/ui';

import {
  CHANNEL_ABOUT_MAX,
  CHANNEL_TITLE_MAX,
  isUploadablePhoto,
  PHOTO_MAX_BYTES,
  PHOTO_SUFFIXES,
  postTextMax,
} from './_channelsShared';
import { CheckRow } from './_CheckRow';
import { FilePicker } from './_shared';

export type ChannelDraft = {
  avatar: File | null;
  title: string;
  about: string;
  isPublic: boolean;
  username: string;
  reactionsOff: boolean;
};

export type PostDraft = { text: string; file: File | null };

/** The `{n}` a public bulk create substitutes with the account's position. */
export const USERNAME_SLOT = '{n}';

// Каналы: two operations, not one form. Creating a channel per account and
// posting into the channels they already own produce different things and are
// ready under different conditions, so the tab picks between them rather than
// pretending one form covers both.
export function BulkChannelsTab({
  mode,
  channel,
  post,
  onMode,
  onChannel,
  onPost,
}: {
  mode: 'create' | 'post';
  channel: ChannelDraft;
  post: PostDraft;
  onMode: (mode: 'create' | 'post') => void;
  onChannel: (draft: ChannelDraft) => void;
  onPost: (draft: PostDraft) => void;
}) {
  const { t } = useTranslation();

  return (
    <div className="flex flex-col gap-4">
      <SegmentedControl
        variant="outline"
        value={mode}
        ariaLabel={t('accounts.bulk.channelMode')}
        options={[
          { value: 'create', label: t('accounts.bulk.channelCreate') },
          { value: 'post', label: t('accounts.bulk.channelPost') },
        ]}
        onChange={(next) => {
          onMode(next as 'create' | 'post');
        }}
      />

      {mode === 'create' ? (
        <>
          <div className="type-body text-content-subtle">
            {t('accounts.bulk.channelCreateHint')}
          </div>
          <div className="flex items-center gap-4">
            {/* The circle IS the upload: an empty one shows the plus only under
                the cursor, so a filled avatar is never covered by a control. */}
            <FilePicker
              accept={PHOTO_SUFFIXES.join(',')}
              multiple={false}
              onPick={(picked) => {
                const file = picked[0];
                if (!file) return;
                if (!isUploadablePhoto(file)) {
                  toastError(
                    t('accounts.bulk.fileRejected', {
                      name: file.name,
                      formats: PHOTO_SUFFIXES.join(', '),
                      mb: PHOTO_MAX_BYTES / 1_000_000,
                    }),
                  );
                  return;
                }
                onChannel({ ...channel, avatar: file });
              }}
            >
              {(open) => (
                <button
                  type="button"
                  aria-label={t('accounts.channel.avatarUpload')}
                  onClick={open}
                  className="group relative flex size-face shrink-0 items-center justify-center overflow-hidden rounded-full border-[1.5px] border-dashed border-line-strong bg-surface-card text-content-muted transition-colors hover:border-action-primary hover:text-action-primary"
                >
                  <span
                    className={channel.avatar ? 'opacity-0' : 'opacity-0 group-hover:opacity-100'}
                  >
                    <Icon name="plus" size={20} />
                  </span>
                  {channel.avatar && (
                    <span className="absolute inset-0 flex items-center justify-center bg-canvas type-small">
                      {t('accounts.bulk.channelAvatarSet')}
                    </span>
                  )}
                </button>
              )}
            </FilePicker>
            <div className="type-small">{t('accounts.bulk.channelAvatarNote')}</div>
          </div>

          <label className="flex flex-col gap-2">
            <span className="type-body-medium text-content-secondary">
              {t('accounts.channel.titleLabel')}
            </span>
            <Input
              value={channel.title}
              maxLength={CHANNEL_TITLE_MAX}
              onChange={(event) => {
                onChannel({ ...channel, title: event.target.value });
              }}
            />
          </label>

          <label className="flex flex-col gap-2">
            <span className="type-body-medium text-content-secondary">
              {t('accounts.channel.aboutLabel')}
            </span>
            <Textarea
              className="[font-family:inherit]"
              value={channel.about}
              maxLength={CHANNEL_ABOUT_MAX}
              onChange={(event) => {
                onChannel({ ...channel, about: event.target.value });
              }}
            />
          </label>

          <CheckRow
            label={t('accounts.channel.publicToggle')}
            on={channel.isPublic}
            onToggle={() => {
              onChannel({ ...channel, isPublic: !channel.isPublic });
            }}
          />

          {channel.isPublic && (
            <label className="flex flex-col gap-2">
              <span className="type-body-medium text-content-secondary">
                {t('accounts.channel.usernameLabel')}
              </span>
              <div className="relative flex items-center">
                <span className="absolute left-4 text-body text-content-subtle">@</span>
                <Input
                  className="pl-8"
                  aria-label={t('accounts.channel.usernameLabel')}
                  value={channel.username}
                  onChange={(event) => {
                    onChannel({ ...channel, username: event.target.value });
                  }}
                />
              </div>
              <span className="type-small">{t('accounts.bulk.channelUsernameNote')}</span>
            </label>
          )}

          <CheckRow
            label={t('accounts.channel.reactionsToggle')}
            on={channel.reactionsOff}
            onToggle={() => {
              onChannel({ ...channel, reactionsOff: !channel.reactionsOff });
            }}
          />
        </>
      ) : (
        <>
          <div className="type-body text-content-subtle">{t('accounts.bulk.channelPostHint')}</div>
          <div className="flex flex-col gap-2">
            <Textarea
              className="[font-family:inherit]"
              value={post.text}
              maxLength={postTextMax(post.file)}
              aria-label={t('accounts.channel.composerPlaceholder')}
              placeholder={t('accounts.channel.composerPlaceholder')}
              onChange={(event) => {
                onPost({ ...post, text: event.target.value });
              }}
            />
            {/* Attaching media AFTER the text drops the ceiling from 4096 to 1024,
                and `maxLength` cannot shorten what is already typed — the counter
                turns red and the footer's Apply goes with it. */}
            <span
              className={`self-end type-small-medium ${post.text.length > postTextMax(post.file) ? 'text-danger-deep' : ''}`}
            >
              {t('accounts.channel.charCount', {
                n: post.text.length,
                max: postTextMax(post.file),
              })}
            </span>
          </div>
          <div className="flex items-center gap-3">
            <FilePicker
              accept={PHOTO_SUFFIXES.join(',')}
              multiple={false}
              onPick={(picked) => {
                const file = picked[0];
                if (!file) return;
                if (!isUploadablePhoto(file)) {
                  toastError(
                    t('accounts.bulk.fileRejected', {
                      name: file.name,
                      formats: PHOTO_SUFFIXES.join(', '),
                      mb: PHOTO_MAX_BYTES / 1_000_000,
                    }),
                  );
                  return;
                }
                onPost({ ...post, file });
              }}
            >
              {(open) => (
                <Button size="xs" variant="dashedMuted" onClick={open}>
                  <Icon name="plus" size={16} />
                  {t('accounts.channel.attach')}
                </Button>
              )}
            </FilePicker>
            {post.file && (
              <span className="min-w-0 flex-1 truncate type-small">{post.file.name}</span>
            )}
            {post.file && (
              <IconButton
                size="sm"
                shape="circle"
                aria-label={t('accounts.channel.removeFile')}
                onClick={() => {
                  onPost({ ...post, file: null });
                }}
                className="shrink-0"
              >
                <Icon name="close" size={16} />
              </IconButton>
            )}
          </div>
          <div className="type-small">{t('accounts.bulk.channelPostNote')}</div>
        </>
      )}
    </div>
  );
}
