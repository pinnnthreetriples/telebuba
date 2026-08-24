import { useTranslation } from 'react-i18next';

import { accountDesignStatus, type AccountStatus } from '../model/status';

// The design's status pill: a coloured dot + label, tinted per status. Colours
// are the design's exact statusMap values (active/spam/code/banned).
const STATUS_CLASS: Record<ReturnType<typeof accountDesignStatus>, string> = {
  active: 'bg-success-tint text-success-deep',
  spam: 'bg-warning-tint text-warning-deep',
  code: 'bg-primary-tint text-primary-deep',
  banned: 'bg-danger-tint text-danger-deep',
};

export function StatusBadge({ status }: { status: AccountStatus }) {
  const { t } = useTranslation();
  return (
    <span
      className={`inline-flex items-center gap-sm rounded-full px-[10px] py-[3px] text-tiny font-medium ${STATUS_CLASS[accountDesignStatus(status)]}`}
    >
      <span className="h-[6px] w-[6px] rounded-full bg-current" />
      {t(`accounts.status.${status}`)}
    </span>
  );
}
