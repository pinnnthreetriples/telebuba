import { useTranslation } from 'react-i18next';

import { Button, Icon, Input, SegmentedControl, Textarea } from '@/shared/ui';

import { CHANNEL_ABOUT_MAX, CHANNEL_TITLE_MAX, PHOTO_SUFFIXES } from './_channelsShared';
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
    <div className="flex flex-col gap-lg">
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
          <div className="type-prose">{t('accounts.bulk.channelCreateHint')}</div>
          <div className="flex items-center gap-lg">
            {/* The circle IS the upload: an empty one shows the plus only under
                the cursor, so a filled avatar is never covered by a control. */}
            <FilePicker
              accept={PHOTO_SUFFIXES.join(',')}
              multiple={false}
              onPick={(picked) => {
                const file = picked[0];
                if (file) onChannel({ ...channel, avatar: file });
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
                    <span className="absolute inset-0 flex items-center justify-center bg-canvas type-caption">
                      {t('accounts.bulk.channelAvatarSet')}
                    </span>
                  )}
                </button>
              )}
            </FilePicker>
            <div className="type-caption">{t('accounts.bulk.channelAvatarNote')}</div>
          </div>

          <label className="flex flex-col gap-tight">
            <span className="type-label">{t('accounts.channel.titleLabel')}</span>
            <Input
              value={channel.title}
              maxLength={CHANNEL_TITLE_MAX}
              onChange={(event) => {
                onChannel({ ...channel, title: event.target.value });
              }}
            />
          </label>

          <label className="flex flex-col gap-tight">
            <span className="type-label">{t('accounts.channel.aboutLabel')}</span>
            <Textarea
              className="resize-none [font-family:inherit]"
              rows={2}
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
            <label className="flex flex-col gap-tight">
              <span className="type-label">{t('accounts.channel.usernameLabel')}</span>
              <div className="relative flex items-center">
                <span className="absolute left-lg text-body text-content-subtle">@</span>
                <Input
                  className="pl-page"
                  aria-label={t('accounts.channel.usernameLabel')}
                  value={channel.username}
                  onChange={(event) => {
                    onChannel({ ...channel, username: event.target.value });
                  }}
                />
              </div>
              <span className="type-caption">{t('accounts.bulk.channelUsernameNote')}</span>
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
          <div className="type-prose">{t('accounts.bulk.channelPostHint')}</div>
          <Textarea
            className="resize-none [font-family:inherit]"
            rows={4}
            value={post.text}
            aria-label={t('accounts.channel.composerPlaceholder')}
            placeholder={t('accounts.channel.composerPlaceholder')}
            onChange={(event) => {
              onPost({ ...post, text: event.target.value });
            }}
          />
          <div className="flex items-center gap-md">
            <FilePicker
              accept={PHOTO_SUFFIXES.join(',')}
              multiple={false}
              onPick={(picked) => {
                const file = picked[0];
                if (file) onPost({ ...post, file });
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
              <span className="min-w-0 flex-1 truncate type-caption">{post.file.name}</span>
            )}
            {post.file && (
              <button
                type="button"
                aria-label={t('accounts.channel.removeFile')}
                onClick={() => {
                  onPost({ ...post, file: null });
                }}
                className="shrink-0 text-title leading-none text-content-muted"
              >
                ×
              </button>
            )}
          </div>
          <div className="type-caption">{t('accounts.bulk.channelPostNote')}</div>
        </>
      )}
    </div>
  );
}
