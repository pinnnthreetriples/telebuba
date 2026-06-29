import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { AccountRead } from '@/shared/api';
import { Modal } from '@/shared/ui';

import { AddStoryModal } from './AddStoryModal';

// The design's profile-edit modal: hero header, a 4-tab segmented header
// (text / photo / stories / music), per-tab bodies, and a save→saved swap
// footer. The Сторис tab opens AddStoryModal above it. ponytail: design-first —
// fields and uploads are presentational; only Save flips the swap state.
type Tab = 'text' | 'photo' | 'stories' | 'music';

const FIELD =
  'tb-time w-full rounded-[10px] border border-line-input bg-white px-3 py-[9px] text-[13px] outline-none';
const LABEL = 'mb-[6px] block text-[12px] font-medium text-[#3a3a3a]';

// ponytail: design-first — placeholder avatar/story tiles so the photo & stories
// tabs render their grids without backend media.
const AVATARS = [
  'linear-gradient(135deg,#7c9cff,#a0e0c0)',
  'linear-gradient(135deg,#ffb3a0,#ffd9a0)',
];
const STORIES = [
  { g: 'linear-gradient(135deg,#a0c4ff,#bdb2ff)', privacy: 'contacts' as const },
  { g: 'linear-gradient(135deg,#ffc6a0,#ff9aa2)', privacy: 'public' as const },
];

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

export function ProfileModal({ account, onClose }: { account: AccountRead; onClose: () => void }) {
  const { t } = useTranslation();
  const [tab, setTab] = useState<Tab>('text');
  const [storyOpen, setStoryOpen] = useState(false);
  const [hasMusic, setHasMusic] = useState(true);
  const [saved, setSaved] = useState(false);

  const initial = (account.first_name ?? account.phone ?? account.account_id)
    .trim()
    .charAt(0)
    .toUpperCase();
  const fullName =
    [account.first_name, account.last_name].filter(Boolean).join(' ') ||
    (account.phone ?? account.account_id);

  const onSave = () => {
    setSaved(true);
    window.setTimeout(() => {
      setSaved(false);
    }, 1400);
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
                {account.username ? `@${account.username} · ` : ''}
                {account.phone ?? account.account_id}
              </div>
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
                    <input defaultValue={account.first_name ?? ''} className={FIELD} />
                  </label>
                  <label>
                    <span className={LABEL}>{t('accounts.profile.lastName')}</span>
                    <input defaultValue={account.last_name ?? ''} className={FIELD} />
                  </label>
                </div>
                <label>
                  <span className={LABEL}>{t('accounts.profile.username')}</span>
                  <div className="relative flex items-center">
                    <span className="absolute left-3 text-[13px] text-ink-subtle">@</span>
                    <input defaultValue={account.username ?? ''} className={`${FIELD} pl-[26px]`} />
                  </div>
                </label>
                <label>
                  <span className={LABEL}>{t('accounts.profile.bio')}</span>
                  <textarea rows={3} className={`${FIELD} resize-none [font-family:inherit]`} />
                </label>
              </div>
            )}

            {tab === 'photo' && (
              <div>
                <div className="mb-3 text-[12px] text-ink-subtle">
                  {t('accounts.profile.photoHint')}
                </div>
                <div className="grid grid-cols-[repeat(auto-fill,minmax(104px,1fr))] gap-3">
                  {AVATARS.map((g, index) => (
                    <div key={g} className="relative">
                      <div
                        className="rounded-[12px] border border-black/5"
                        style={{ aspectRatio: '1', background: g }}
                      />
                      <button
                        type="button"
                        aria-label={t('accounts.profile.removePhoto')}
                        className="absolute right-[6px] top-[6px] h-[22px] w-[22px] rounded-full bg-[rgba(11,11,12,0.55)] text-[13px] leading-none text-white"
                      >
                        ×
                      </button>
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
                    onClick={() => undefined}
                  />
                </div>
              </div>
            )}

            {tab === 'stories' && (
              <div>
                <div className="mb-3 text-[12px] text-ink-subtle">
                  {t('accounts.profile.storiesHint')}
                </div>
                <div className="grid grid-cols-[repeat(auto-fill,minmax(96px,1fr))] gap-3">
                  {STORIES.map((story) => (
                    <div key={story.g} className="relative">
                      <div
                        className="rounded-[12px] border border-black/5"
                        style={{ aspectRatio: '9 / 16', background: story.g }}
                      />
                      <button
                        type="button"
                        aria-label={t('accounts.profile.removeStory')}
                        className="absolute right-[6px] top-[6px] h-[22px] w-[22px] rounded-full bg-[rgba(11,11,12,0.55)] text-[13px] leading-none text-white"
                      >
                        ×
                      </button>
                      <span className="absolute inset-x-[5px] bottom-[5px] truncate rounded-[6px] bg-[rgba(11,11,12,0.6)] px-[5px] py-[2px] text-center text-[9px] font-medium text-white">
                        {t(`accounts.addStory.${story.privacy}`)}
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
                {hasMusic ? (
                  <div className="flex items-center gap-[13px] rounded-[12px] border border-line px-[14px] py-3">
                    <button
                      type="button"
                      aria-label={t('accounts.profile.play')}
                      className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-primary text-white"
                    >
                      <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor">
                        <path d="M8 5v14l11-7z" />
                      </svg>
                    </button>
                    <div className="min-w-0 flex-1">
                      <div className="text-[13.5px] font-semibold">
                        {t('accounts.profile.trackTitle')}
                      </div>
                      <div className="text-[12px] text-ink-subtle">
                        {t('accounts.profile.trackArtist')}
                      </div>
                    </div>
                    <button
                      type="button"
                      onClick={() => {
                        setHasMusic(false);
                      }}
                      aria-label={t('accounts.profile.removeMusic')}
                      className="h-[30px] w-[30px] rounded-full border border-line bg-white text-[15px] text-ink-subtle"
                    >
                      ×
                    </button>
                  </div>
                ) : (
                  <div className="rounded-[12px] border border-dashed border-line bg-white px-4 py-6 text-center text-[12.5px] text-ink-subtle">
                    {t('accounts.profile.noMusic')}
                  </div>
                )}
                <button
                  type="button"
                  onClick={() => {
                    setHasMusic(true);
                  }}
                  className="mt-3 rounded-full border border-line-input bg-white px-4 py-2 text-[13px] font-medium"
                >
                  {t('accounts.profile.pickTrack')}
                </button>
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
              className={`rounded-full px-[22px] py-[9px] text-[13px] font-medium text-white transition-colors ${saved ? 'bg-[#2e9e64]' : 'bg-primary'}`}
            >
              {saved ? (
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
          onClose={() => {
            setStoryOpen(false);
          }}
        />
      )}
    </>
  );
}
