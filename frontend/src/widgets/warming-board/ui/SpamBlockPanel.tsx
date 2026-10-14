import { useTranslation } from 'react-i18next';

import { Button, Icon, Notice } from '@/shared/ui';

// The card of an account warming stopped for good: @SpamBot confirmed its spam block on
// the quarantine re-check. Self-contained on purpose — the whole decision lives here:
// «Удалить» opens the ordinary account delete dialog, «Оставить» keeps the account (off
// the problem list, a permanent «Спамблок» badge, warmable again without DMs).
export function SpamBlockPanel({
  busy,
  onDelete,
  onKeep,
}: {
  busy: boolean;
  onDelete: () => void;
  onKeep: () => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="mt-3">
      <Notice tone="danger" role="status" className="flex flex-col gap-3 py-3">
        <div className="flex items-start gap-2">
          <Icon name="alert-triangle" size={14} className="mt-1 shrink-0" />
          <div className="min-w-0">
            <div className="type-body-medium">{t('warming.spamBlock.title')}</div>
            <div className="type-small">{t('warming.spamBlock.body')}</div>
          </div>
        </div>
        <div className="flex flex-wrap justify-end gap-2">
          <Button size="sm" variant="danger" disabled={busy} onClick={onDelete}>
            {t('warming.spamBlock.delete')}
          </Button>
          <Button size="sm" disabled={busy} onClick={onKeep}>
            {t('warming.spamBlock.keep')}
          </Button>
        </div>
      </Notice>
    </div>
  );
}
