import { useTranslation } from 'react-i18next';

import { Button } from '@/shared/ui';

// Step 1 of the QR method: no input to collect, only the go-ahead that creates the
// placeholder account. Like the phone panel, success unlocks "Next" and nothing
// connects to Telegram until the proxy step is behind the operator.
export function QrStartPanel({
  pending,
  created,
  failed,
  onStart,
}: {
  pending: boolean;
  created: boolean;
  failed: boolean;
  onStart: () => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="tb-fadeup flex flex-col gap-3 rounded-md border border-line bg-surface-card px-3 py-4">
      <div className="type-body text-content-subtle">{t('accounts.addWizard.qrInstructions')}</div>
      <Button
        variant="primary"
        size="sm"
        className="self-start"
        onClick={onStart}
        disabled={pending || created}
      >
        {pending
          ? t('accounts.addWizard.qrCreating')
          : created
            ? t('accounts.addWizard.qrCreated')
            : t('accounts.addWizard.qrContinue')}
      </Button>
      {failed && (
        <div className="type-small text-danger-deep">{t('accounts.addWizard.qrError')}</div>
      )}
    </div>
  );
}
