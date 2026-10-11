import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { accountDeleteReasonQueryOptions } from '@/entities/account';
import type { AccountDeleteReasonGuess } from '@/shared/api';
import { Button, Modal, Select } from '@/shared/ui';

export type DeleteReason = AccountDeleteReasonGuess['reason'];

const REASONS: readonly DeleteReason[] = ['dead', 'retired'];

// The design's delete-account confirm dialog. The reason goes to the lifecycle
// journal; it is pre-selected from the server's guess (limited or dead session →
// dead, otherwise retired) and the operator may change it. Confirming before the
// guess arrives sends no reason, and the server journals its own guess.
export function DeleteAccountModal({
  accountId,
  phone,
  onClose,
  onConfirm,
}: {
  accountId: string;
  phone: string;
  onClose: () => void;
  onConfirm: (reason: DeleteReason | undefined) => void;
}) {
  const { t } = useTranslation();
  const guess = useQuery(accountDeleteReasonQueryOptions({ path: { account_id: accountId } }));
  const [chosen, setChosen] = useState<DeleteReason | null>(null);
  const reason = chosen ?? guess.data?.reason;
  const reasonLabel = t('accounts.deleteModal.reasonLabel');
  return (
    <Modal onClose={onClose} size="confirm" label={t('accounts.deleteModal.title', { phone })}>
      <div className="p-6">
        <div className="mb-2 type-h2">{t('accounts.deleteModal.title', { phone })}</div>
        <div className="mb-4 type-body text-content-muted">{t('accounts.deleteModal.body')}</div>
        <div className="mb-6">
          <span className="mb-2 block type-body-medium text-content-secondary">{reasonLabel}</span>
          <Select
            value={reason ?? ''}
            onChange={(value) => {
              setChosen(value as DeleteReason);
            }}
            options={REASONS.map((value) => ({
              value,
              label: t(`accounts.deleteModal.reason.${value}`),
            }))}
            placeholder={t('accounts.deleteModal.reasonPending')}
            ariaLabel={reasonLabel}
          />
        </div>
        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>{t('accounts.deleteModal.cancel')}</Button>
          <Button
            variant="danger"
            onClick={() => {
              onConfirm(reason);
              onClose();
            }}
          >
            {t('accounts.deleteModal.confirm')}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
