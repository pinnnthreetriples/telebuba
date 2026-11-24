import { useTranslation } from 'react-i18next';

import { Button, Notice } from '@/shared/ui';

import type { InterruptedRun } from './useResumableRun';

// An earlier bulk run stopped before it reached every account (the tab was closed): say how
// far it got and let the operator go on with the rest or forget it.
export function ResumeNotice({
  run,
  onContinue,
  onReset,
  busy = false,
}: {
  run: InterruptedRun;
  onContinue: () => void;
  onReset: () => void;
  busy?: boolean;
}) {
  const { t } = useTranslation();
  return (
    <div className="mb-4">
      <Notice tone="warning" role="status" className="flex flex-wrap items-center gap-3">
        <span className="min-w-0 flex-1 text-small font-medium">
          {t('accounts.addWizard.interrupted', { done: run.processed, total: run.total })}
        </span>
        <div className="flex shrink-0 gap-2">
          <Button size="sm" variant="primary" disabled={busy} onClick={onContinue}>
            {t('accounts.addWizard.interruptedContinue')}
          </Button>
          <Button size="sm" disabled={busy} onClick={onReset}>
            {t('accounts.addWizard.interruptedReset')}
          </Button>
        </div>
      </Notice>
    </div>
  );
}
