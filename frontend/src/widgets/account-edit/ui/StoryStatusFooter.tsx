import { useTranslation } from 'react-i18next';

import { Icon, Notice, ProgressBar, Spinner } from '@/shared/ui';

import { downgradedList } from './_storyShared';

// Per-publish status: uploading spinner + bar, then success check + full bar, or an error
// icon (hover = reason) + retry; and, after a success, what the account's missing Premium
// cost the story. Shared by both modes.
export function StoryStatusFooter({
  busy,
  done,
  failed,
  metaText,
  metaTone,
  errorDetail,
  onRetry,
  retryDisabled,
  downgraded,
  scheduled,
}: {
  busy: boolean;
  done: boolean;
  failed: boolean;
  metaText: string;
  metaTone: string;
  errorDetail: string;
  onRetry: () => void;
  retryDisabled: boolean;
  downgraded: readonly string[];
  scheduled: boolean;
}) {
  const { t } = useTranslation();
  if (!busy && !done && !failed) return null;
  return (
    <>
      <div className="mt-3 tb-fadeup flex items-center gap-3 rounded-md border border-line bg-surface-card px-3 py-3">
        <div className="min-w-0 flex-1">
          <div className={`type-small-medium ${metaTone}`}>{metaText}</div>
          {(busy || done) && (
            <ProgressBar className="mt-2" tone="success" value={1} max={1} indeterminate={!done} />
          )}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {busy && <Spinner className="m-1" />}
          {done && (
            <span className="tb-pop m-1 inline-flex text-success-deep">
              <Icon name="check-circle" size={18} />
            </span>
          )}
          {failed && (
            <>
              <span className="group relative m-1 inline-flex text-danger">
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
                <span
                  role="tooltip"
                  className="pointer-events-none absolute right-0 top-[calc(100%+6px)] z-pop hidden w-max max-w-name whitespace-normal rounded-sm bg-term px-3 py-2 text-left text-small font-normal text-on-fill shadow-pop group-hover:block"
                >
                  {errorDetail}
                </span>
              </span>
              <button
                type="button"
                onClick={onRetry}
                disabled={retryDisabled}
                aria-label={t('accounts.addStory.retry')}
                className="inline-flex size-chip items-center justify-center rounded-full text-content-muted"
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
        </div>
      </div>
      {done && downgraded.length > 0 && (
        <div className="mt-3">
          <Notice tone="warning" role="status">
            {t(
              scheduled
                ? 'accounts.addStory.downgraded.scheduled'
                : 'accounts.addStory.downgraded.posted',
              { list: downgradedList(t, downgraded) },
            )}
          </Notice>
        </div>
      )}
    </>
  );
}
