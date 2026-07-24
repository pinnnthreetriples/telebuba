import { useTranslation } from 'react-i18next';

import type { ProfileStoryView } from '@/shared/api';

import { tileStyle } from './_profileShared';
import { DashedAdd } from './_shared';

// The profile modal's stories tab: 9:16 tiles with view/reaction badges, a
// pin/unpin toggle, a privacy badge, and remove — plus the add-story tile.
export function StoriesTab({
  stories,
  pinPending,
  onAdd,
  onRemove,
  onPinToggle,
}: {
  stories: ProfileStoryView[];
  pinPending: boolean;
  onAdd: () => void;
  onRemove: (story: ProfileStoryView) => void;
  onPinToggle: (story: ProfileStoryView) => void;
}) {
  const { t } = useTranslation();
  return (
    <div>
      <div className="mb-3 text-[12px] text-ink-subtle">{t('accounts.profile.storiesHint')}</div>
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
                onRemove(story);
              }}
              className="absolute right-[6px] top-[6px] h-[22px] w-[22px] rounded-full bg-[rgba(11,11,12,0.55)] text-[13px] leading-none text-white"
            >
              ×
            </button>
            <button
              type="button"
              disabled={pinPending}
              aria-label={t(
                story.is_pinned ? 'accounts.profile.unpinStory' : 'accounts.profile.pinStory',
              )}
              onClick={() => {
                onPinToggle(story);
              }}
              className={`absolute inset-x-[5px] bottom-[24px] truncate rounded-[6px] px-[5px] py-[2px] text-center text-[9px] font-medium disabled:opacity-50 ${
                story.is_pinned ? 'bg-primary text-white' : 'bg-[rgba(11,11,12,0.6)] text-white'
              }`}
            >
              {t(story.is_pinned ? 'accounts.profile.pinnedForever' : 'accounts.profile.pin24h')}
            </button>
            <span className="absolute inset-x-[5px] bottom-[5px] truncate rounded-[6px] bg-[rgba(11,11,12,0.6)] px-[5px] py-[2px] text-center text-[9px] font-medium text-white">
              {t(`accounts.addStory.${story.privacy_preset ?? 'unknown'}`)}
            </span>
          </div>
        ))}
        <DashedAdd ratio="9 / 16" label={t('accounts.profile.addStory')} onClick={onAdd} />
      </div>
    </div>
  );
}
