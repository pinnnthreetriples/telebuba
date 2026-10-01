import { useTranslation } from 'react-i18next';

import { SegmentedControl } from '@/shared/ui';

export type ScheduleMode = 'now' | 'later';

// "Сейчас / По расписанию": whether a publish goes out on the click or waits for
// the time the operator picks. "Now" stays the default everywhere it appears.
export function ScheduleModeControl({
  value,
  onChange,
  disabled = false,
  className,
}: {
  value: ScheduleMode;
  onChange: (mode: ScheduleMode) => void;
  disabled?: boolean;
  className?: string;
}) {
  const { t } = useTranslation();
  return (
    <SegmentedControl
      className={className}
      value={value}
      disabled={disabled}
      ariaLabel={t('accounts.schedule.mode')}
      options={[
        { value: 'now', label: t('accounts.schedule.now') },
        { value: 'later', label: t('accounts.schedule.later') },
      ]}
      onChange={onChange}
    />
  );
}
