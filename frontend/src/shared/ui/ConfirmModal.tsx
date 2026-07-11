import { useState } from 'react';

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
    <Modal onClose={onClose} z={80} className="w-[420px]">
      <div className="p-6">
        <div className="mb-2 text-[16px] font-bold">{title}</div>
        <div className="mb-[22px] text-[13px] leading-[1.5] text-ink-muted">{body}</div>
        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-full border border-line-input bg-white px-[18px] py-[9px] text-[13px] font-medium text-ink"
          >
            {cancelLabel}
          </button>
          <button
            type="button"
            onClick={confirm}
            disabled={pending}
            className="rounded-full border border-[#f0c9c5] bg-danger-tint px-5 py-[9px] text-[13px] font-semibold text-danger disabled:opacity-60"
          >
            {pending ? (
              <span className="inline-flex items-center gap-[6px]">
                <span className="tb-spin inline-block h-[13px] w-[13px] rounded-full border-2 border-[#f0c9c5] border-t-danger" />
                {confirmLabel}
              </span>
            ) : (
              confirmLabel
            )}
          </button>
        </div>
      </div>
    </Modal>
  );
}
