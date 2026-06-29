import { useTranslation } from 'react-i18next';

import { accountDesignStatus, type AccountStatus } from '../model/status';

// The design's status pill: a coloured dot + label, tinted per status. Colours
// are the design's exact statusMap values (active/spam/code/banned).
const STATUS_CLASS: Record<ReturnType<typeof accountDesignStatus>, string> = {
  active: 'bg-[#ddf7e9] text-[#12a150]',
  spam: 'bg-[#fff0d2] text-[#e08700]',
  code: 'bg-[#e1ecff] text-[#0066ff]',
  banned: 'bg-[#fde6e2] text-[#e5372a]',
};

export function StatusBadge({ status }: { status: AccountStatus }) {
  const { t } = useTranslation();
  return (
    <span
      className={`inline-flex items-center gap-[6px] rounded-full px-[10px] py-[3px] text-[12px] font-medium ${STATUS_CLASS[accountDesignStatus(status)]}`}
    >
      <span className="h-[6px] w-[6px] rounded-full bg-current" />
      {t(`accounts.status.${status}`)}
    </span>
  );
}
