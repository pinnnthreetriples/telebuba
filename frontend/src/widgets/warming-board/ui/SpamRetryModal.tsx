import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Button, Modal } from '@/shared/ui';

// The hand-off to neurocomment needs an answer from @SpamBot, and there is
// deliberately no "hand off anyway": the only way forward is to ask again. The
// retry's promise rejects while @SpamBot still says nothing, which keeps the dialog
// open; it resolves on success (or on any other refusal, which the page toasts).
export function SpamRetryModal({
  onClose,
  onRetry,
}: {
  onClose: () => void;
  onRetry: () => Promise<unknown>;
}) {
  const { t } = useTranslation();
  const [pending, setPending] = useState(false);
  const retry = () => {
    setPending(true);
    onRetry().then(onClose, () => {
      setPending(false);
    });
  };
  return (
    <Modal onClose={onClose} size="confirm" label={t('warming.spamRetry.title')}>
      <div className="p-6">
        <div className="mb-2 type-h3">{t('warming.spamRetry.title')}</div>
        <div className="mb-6 type-body text-content-muted">{t('warming.spamRetry.body')}</div>
        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>{t('warming.spamRetry.close')}</Button>
          <Button variant="primary" onClick={retry} loading={pending}>
            {t('warming.spamRetry.retry')}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
