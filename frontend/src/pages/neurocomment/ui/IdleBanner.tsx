import { useTranslation } from 'react-i18next';

// Amber nudge shown when graduated ("Прогреты") accounts aren't yet linked to the
// selected campaign — clicking opens the accounts modal to assign them.
export function IdleBanner({ count, onOpen }: { count: number; onOpen: () => void }) {
  const { t } = useTranslation();
  return (
    <button
      type="button"
      onClick={onOpen}
      className="flex items-center gap-md rounded-lg border border-warning-line bg-warning-tint px-lg py-md text-left transition-colors hover:border-warning"
    >
      <span className="flex h-[30px] w-[30px] shrink-0 items-center justify-center rounded-md bg-warning-line text-warning-deep">
        <svg
          width="16"
          height="16"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <circle cx="12" cy="12" r="9" />
          <path d="M12 7v5l3 2" />
        </svg>
      </span>
      <div className="min-w-0 flex-1">
        <div className="text-body font-bold leading-[1.25] text-warning-deep">
          {t('neurocomment.idle.label', { count })}
        </div>
        {/* Grey under an amber heading, for the reason WarmingBoard's twin is grey: the
            amber is already said by the heading, the chip and the surface, and `warning`
            here reached only 3.57:1 against this tint where `ink-body` reaches 10.10:1. */}
        <div className="mt-px text-tiny text-ink-body">{t('neurocomment.idle.sub')}</div>
      </div>
      <span className="flex shrink-0 text-warning">
        <svg
          width="16"
          height="16"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
        >
          <path d="m9 18 6-6-6-6" />
        </svg>
      </span>
    </button>
  );
}
