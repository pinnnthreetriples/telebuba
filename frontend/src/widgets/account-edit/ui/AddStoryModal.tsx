import { useMutation } from '@tanstack/react-query';
import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { postAccountStoryMutation } from '@/entities/account';
import { Modal } from '@/shared/ui';

// The design's new-story modal: audience segmented control, caption, a
// no-forward checkbox, and a media dropzone. Opened above the profile modal
// (z=75). Publishing posts a real story (postAccountStory) for the account.
type Audience = 'contacts' | 'closeFriends' | 'public';

const PRIVACY: Record<Audience, 'contacts' | 'close_friends' | 'public'> = {
  contacts: 'contacts',
  closeFriends: 'close_friends',
  public: 'public',
};

const FIELD =
  'tb-time w-full rounded-[10px] border border-line-input bg-white px-3 py-[9px] text-[13px] outline-none';

export function AddStoryModal({
  accountId,
  onClose,
  onPosted,
}: {
  accountId: string;
  onClose: () => void;
  onPosted: () => void;
}) {
  const { t } = useTranslation();
  const [audience, setAudience] = useState<Audience>('contacts');
  const [noForward, setNoForward] = useState(false);
  const [caption, setCaption] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const post = useMutation(postAccountStoryMutation());

  const seg = (on: boolean): string =>
    `flex-1 rounded-[7px] py-[7px] text-[12.5px] font-medium transition ${on ? 'bg-white text-ink shadow-sm' : 'text-ink-muted'}`;

  const publish = () => {
    if (!file) return;
    post.mutate(
      {
        path: { account_id: accountId },
        body: {
          file,
          media_kind: file.type.startsWith('video') ? 'video' : 'image',
          caption: caption.trim() || null,
          privacy_preset: PRIVACY[audience],
          protect_content: noForward,
        },
      },
      {
        onSuccess: () => {
          onPosted();
          onClose();
        },
      },
    );
  };

  return (
    <Modal onClose={onClose} z={75} backdrop={0.45} className="w-[460px]">
      <div className="tb-scroll max-h-[88vh] overflow-y-auto px-6 py-[22px]">
        <div className="mb-4 flex items-center justify-between">
          <span className="text-[16px] font-bold">{t('accounts.addStory.title')}</span>
          <button
            type="button"
            onClick={onClose}
            aria-label={t('accounts.addStory.close')}
            className="h-[30px] w-[30px] rounded-full border border-line bg-white text-[16px] text-ink-muted"
          >
            ×
          </button>
        </div>

        <div className="mb-[6px] text-[12px] font-medium text-[#3a3a3a]">
          {t('accounts.addStory.audience')}
        </div>
        <div className="mb-[14px] flex gap-1 rounded-[10px] bg-[#f1efed] p-1">
          {(['contacts', 'closeFriends', 'public'] as const).map((value) => (
            <button
              key={value}
              type="button"
              onClick={() => {
                setAudience(value);
              }}
              className={seg(audience === value)}
            >
              {t(`accounts.addStory.${value}`)}
            </button>
          ))}
        </div>

        <label className="mb-[14px] block">
          <span className="mb-[6px] block text-[12px] font-medium text-[#3a3a3a]">
            {t('accounts.addStory.caption')}
          </span>
          <input
            value={caption}
            onChange={(event) => {
              setCaption(event.target.value);
            }}
            placeholder={t('accounts.addStory.captionPlaceholder')}
            className={FIELD}
          />
        </label>

        <button
          type="button"
          onClick={() => {
            setNoForward((value) => !value);
          }}
          className="mb-4 flex w-full items-center gap-[10px] text-left"
        >
          <span
            className={`flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-[5px] border ${noForward ? 'border-primary bg-primary' : 'border-line-input bg-white'}`}
          >
            {noForward && (
              <svg
                width="13"
                height="13"
                viewBox="0 0 24 24"
                fill="none"
                stroke="#fff"
                strokeWidth="3"
              >
                <path d="M20 6 9 17l-5-5" />
              </svg>
            )}
          </span>
          <span className="text-[13px] text-[#3a3a3a]">{t('accounts.addStory.noForward')}</span>
        </button>

        <div className="mb-[6px] text-[12px] font-medium text-[#3a3a3a]">
          {t('accounts.addStory.media')}
        </div>
        <button
          type="button"
          onClick={() => fileInput.current?.click()}
          className="flex w-full items-center gap-[11px] rounded-[12px] border border-dashed border-line bg-white px-4 py-[14px] text-left"
        >
          <div className="flex h-[42px] w-[42px] shrink-0 items-center justify-center rounded-[11px] border border-line bg-white text-primary">
            <svg
              width="20"
              height="20"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.7"
            >
              <rect x="3" y="3" width="18" height="18" rx="3" />
              <path d="M3 15l5-5 4 4M14 14l3-3 4 4" />
              <circle cx="9" cy="9" r="1.6" />
            </svg>
          </div>
          <div className="min-w-0 flex-1">
            <div className="truncate text-[12.5px] font-semibold">
              {file ? file.name : t('accounts.addStory.dropTitle')}
            </div>
            <div className="mt-px text-[11px] text-ink-subtle">
              {t('accounts.addStory.dropHint')}
            </div>
          </div>
        </button>
        <input
          ref={fileInput}
          type="file"
          accept="image/*,video/*"
          className="hidden"
          onChange={(event) => {
            setFile(event.target.files?.[0] ?? null);
            event.target.value = '';
          }}
        />

        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-full border border-line-input bg-white px-[18px] py-[9px] text-[13px] font-medium text-ink"
          >
            {t('accounts.addStory.cancel')}
          </button>
          <button
            type="button"
            onClick={publish}
            disabled={!file || post.isPending}
            className="rounded-full bg-primary px-5 py-[9px] text-[13px] font-medium text-white disabled:opacity-50"
          >
            {t('accounts.addStory.publish')}
          </button>
        </div>
      </div>
    </Modal>
  );
}
