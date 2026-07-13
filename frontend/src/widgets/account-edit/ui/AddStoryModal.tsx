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

function fileSize(
  file: File | null,
  t: (key: string, opts: Record<string, unknown>) => string,
): string {
  if (!file) return '';
  if (file.size >= 1_048_576)
    return t('accounts.addStory.sizeMb', { n: (file.size / 1_048_576).toFixed(1) });
  return t('accounts.addStory.sizeKb', { n: Math.max(1, Math.round(file.size / 1024)) });
}

// Pull the reason out of the /api/v1 error envelope ({error:{code,message}}) the
// failed publish rejects with, so the hover tooltip shows *why* it failed.
// Known locale-neutral failure codes (story_image_invalid / story_video_invalid)
// translate via accounts.addStory.code.*; anything else shows as-is.
function errorText(
  err: unknown,
  t: (key: string, opts?: Record<string, unknown>) => string,
  fallback: string,
): string {
  const message = (err as { error?: { message?: unknown } } | null)?.error?.message;
  if (typeof message !== 'string' || !message.trim()) return fallback;
  return t(`accounts.addStory.code.${message}`, { defaultValue: message });
}

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
  const busy = post.isPending;
  const done = post.isSuccess;
  const failed = post.isError;

  let metaText = fileSize(file, t);
  let metaColor = '#9a9893';
  if (failed) {
    metaText = t('accounts.addStory.stError');
    metaColor = '#c0473f';
  } else if (done) {
    metaText = t('accounts.addStory.stDone');
    metaColor = '#2e9e64';
  } else if (busy) {
    metaText = t('accounts.addStory.stUploading');
  }
  const errorDetail = errorText(post.error, t, t('accounts.addStory.stError'));

  const seg = (on: boolean): string =>
    `flex-1 rounded-[7px] py-[7px] text-[12.5px] font-medium transition ${on ? 'bg-white text-ink shadow-sm' : 'text-ink-muted'}`;

  const publish = () => {
    if (!file) return;
    post.mutate(
      {
        path: { account_id: accountId },
        body: {
          files: [file],
          media_kind: file.type.startsWith('video') ? 'video' : 'image',
          caption: caption.trim() || null,
          privacy_preset: PRIVACY[audience],
          protect_content: noForward,
        },
      },
      {
        // Hold the modal open a beat so the success (tb-pop check + full bar)
        // animation plays before the profile refresh + close, per the design.
        onSuccess: () => {
          onPosted();
          window.setTimeout(onClose, 900);
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
              {t('accounts.addStory.dropTitle')}
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
            post.reset();
            event.target.value = '';
          }}
        />

        {/* Selected-file row: idle size → uploading spinner + progress bar →
            success check / error + retry (the design's per-file publish animation). */}
        {file && (
          <div className="mt-[9px] flex flex-col gap-2">
            <div className="tb-fadeup rounded-[11px] border border-line bg-white px-[11px] py-[10px]">
              <div className="flex items-center gap-[10px]">
                <div className="flex h-[34px] w-[34px] shrink-0 items-center justify-center rounded-[9px] bg-[#f4f3f0] text-ink-muted">
                  <svg
                    width="16"
                    height="16"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.7"
                  >
                    <rect x="3" y="3" width="18" height="18" rx="3" />
                    <path d="M3 15l5-5 4 4" />
                    <circle cx="9" cy="9" r="1.6" />
                  </svg>
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="truncate text-[12px] font-semibold">{file.name}</div>
                      <div className="mt-px text-[10.5px]" style={{ color: metaColor }}>
                        {metaText}
                      </div>
                    </div>
                    <div className="flex shrink-0 items-center gap-[2px]">
                      {busy && (
                        <span className="tb-spin m-[5px] inline-block h-[13px] w-[13px] rounded-full border-2 border-line-input border-t-primary" />
                      )}
                      {done && (
                        <span className="tb-pop m-[3px] inline-flex text-[#2e9e64]">
                          <svg
                            width="17"
                            height="17"
                            viewBox="0 0 24 24"
                            fill="none"
                            stroke="currentColor"
                            strokeWidth="2"
                          >
                            <circle cx="12" cy="12" r="10" />
                            <path d="m8 12 2.5 2.5L16 9" />
                          </svg>
                        </span>
                      )}
                      {failed && (
                        <>
                          <span className="group relative m-[3px] inline-flex text-[#c0473f]">
                            <svg
                              width="17"
                              height="17"
                              viewBox="0 0 24 24"
                              fill="none"
                              stroke="currentColor"
                              strokeWidth="2"
                            >
                              <circle cx="12" cy="12" r="10" />
                              <path d="M12 8v4M12 16h.01" />
                            </svg>
                            {/* Hover reveals *why* the publish failed (the backend
                                error message), per the design's error tooltip. */}
                            <span
                              role="tooltip"
                              className="pointer-events-none absolute right-0 top-[calc(100%+6px)] z-30 hidden w-max max-w-[240px] whitespace-normal rounded-[8px] bg-[#16161a] px-[10px] py-[7px] text-left text-[11px] font-normal leading-[1.5] text-white shadow-[0_6px_20px_rgba(0,0,0,0.18)] group-hover:block"
                            >
                              {errorDetail}
                            </span>
                          </span>
                          <button
                            type="button"
                            onClick={publish}
                            aria-label={t('accounts.addStory.retry')}
                            className="inline-flex h-[25px] w-[25px] items-center justify-center rounded-full text-ink-muted"
                          >
                            <svg
                              width="13"
                              height="13"
                              viewBox="0 0 24 24"
                              fill="none"
                              stroke="currentColor"
                              strokeWidth="1.9"
                            >
                              <path d="M3 2v6h6" />
                              <path d="M3 8a9 9 0 1 0 2.5-3.5L3 8" />
                            </svg>
                          </button>
                        </>
                      )}
                      {!busy && !done && (
                        <button
                          type="button"
                          onClick={() => {
                            setFile(null);
                            post.reset();
                          }}
                          aria-label={t('accounts.addStory.removeFile')}
                          className="inline-flex h-[25px] w-[25px] items-center justify-center rounded-full text-ink-subtle"
                        >
                          <svg
                            width="13"
                            height="13"
                            viewBox="0 0 24 24"
                            fill="none"
                            stroke="currentColor"
                            strokeWidth="2"
                          >
                            <path d="M18 6 6 18M6 6l12 12" />
                          </svg>
                        </button>
                      )}
                    </div>
                  </div>
                  {(busy || done) && (
                    <div className="mt-2 h-[5px] overflow-hidden rounded-full bg-[#eeedea]">
                      <div
                        className={`h-full rounded-full ${done ? 'w-full bg-[#2e9e64]' : 'tb-upbar bg-primary'}`}
                      />
                    </div>
                  )}
                </div>
              </div>
            </div>
          </div>
        )}

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
