import { useState } from 'react';

import { dialogBody, dialogTitle } from '@/shared/design-system';

import { Button } from './Button';
import { Modal } from './Modal';
import { ModalBody, ModalFooter } from './ModalParts';

// Generic delete/remove confirm dialog (rule: any destructive action asks
// first). Mirrors DeleteAccountModal/ProxyDeleteModal/CampaignDeleteModal's
// layout for call sites that don't need their own bespoke copy.
//
// `onConfirm` may return a Promise: the confirm button then shows a pending
// spinner, the dialog closes only when the promise resolves, and stays open on
// rejection (the global mutation toast reports the failure). Sync callers keep
// the old confirm-then-close behaviour.
export function ConfirmModal({
  title,
  body,
  confirmLabel,
  cancelLabel,
  onClose,
  onConfirm,
}: {
  title: string;
  body: string;
  confirmLabel: string;
  cancelLabel: string;
  onClose: () => void;
  onConfirm: () => void | Promise<unknown>;
}) {
  const [pending, setPending] = useState(false);

  const confirm = () => {
    const result = onConfirm();
    if (!(result instanceof Promise)) {
      onClose();
      return;
    }
    setPending(true);
    result.then(onClose, () => {
      setPending(false);
    });
  };

  return (
    <Modal onClose={onClose} size="confirm" label={title}>
      <ModalBody variant="form">
        <div className={dialogTitle()}>{title}</div>
        <div className={dialogBody()}>{body}</div>
        <ModalFooter variant="plain">
          <Button onClick={onClose}>{cancelLabel}</Button>
          <Button variant="danger" onClick={confirm} loading={pending}>
            {confirmLabel}
          </Button>
        </ModalFooter>
      </ModalBody>
    </Modal>
  );
}
