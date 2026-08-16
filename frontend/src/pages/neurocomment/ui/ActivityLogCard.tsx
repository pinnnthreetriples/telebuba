import { useTranslation } from 'react-i18next';

import type { LogEntry } from '@/shared/api';
import { LogTerminal } from '@/widgets/log-terminal';

// The neurocomment activity terminal — the tail of the live log stream.
//
// The terminal itself is `widgets/log-terminal`: the neuroshilling launch card
// needs the same one, and steiger bars it from importing this page. All that is
// left here is the title, which is the only thing the two callers differ on.
export function ActivityLogCard({
  logLines,
  onClear,
  accountName,
}: {
  logLines: LogEntry[];
  onClear?: () => void;
  accountName?: (accountId: string) => string;
}) {
  const { t } = useTranslation();
  return (
    <LogTerminal
      title={t('neurocomment.log.title')}
      logLines={logLines}
      onClear={onClear}
      accountName={accountName}
    />
  );
}
