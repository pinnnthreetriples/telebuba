import { useTranslation } from 'react-i18next';

// Amber nudge shown when graduated ("Прогреты") accounts aren't yet linked to the
// selected campaign — clicking opens the accounts modal to assign them.
export function IdleBanner({ count, onOpen }: { count: number; onOpen: () => void }) {
  const { t } = useTranslation();
  return (
    <button
      type="button"
      onClick={onOpen}
      className="flex items-center gap-[11px] rounded-[14px] border border-[#efd79a] bg-[#fffbef] px-[14px] py-3 text-left transition-colors hover:bg-[#fdf6e3]"
    >
      <span className="flex h-[30px] w-[30px] shrink-0 items-center justify-center rounded-[9px] bg-[#fbefcb] text-[#9a7b22]">
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
        <div className="text-[12.5px] font-bold leading-[1.25] text-[#7a5e12]">
          {t('neurocomment.idle.label', { count })}
        </div>
        <div className="mt-px text-[11px] text-[#a98a2e]">{t('neurocomment.idle.sub')}</div>
      </div>
      <span className="flex shrink-0 text-[#b8922f]">
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
