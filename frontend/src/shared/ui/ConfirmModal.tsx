import { useState } from 'react';

import { Button } from './Button';
import { Modal } from './Modal';

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
    <Modal onClose={onClose} className="w-[420px]" label={title}>
      <div className="p-6">
        <div className="mb-2 text-title font-bold">{title}</div>
        <div className="mb-[22px] text-lead leading-[1.5] text-ink-muted">{body}</div>
        <div className="flex justify-end gap-sm">
          <Button onClick={onClose}>{cancelLabel}</Button>
          <Button variant="danger" onClick={confirm} loading={pending}>
            {pending ? (
              <span className="inline-flex items-center gap-sm">
                <span className="tb-spin inline-block h-[13px] w-[13px] rounded-full border-2 border-danger-line border-t-danger" />
                {confirmLabel}
              </span>
            ) : (
              confirmLabel
            )}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
