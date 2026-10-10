import { Button } from './Button';

// The inside of every two-button question dialog: title, body, then Cancel and the
// danger action. One layout shared by ConfirmModal and by Modal's own "close without
// saving?" guard — Modal cannot render ConfirmModal (that one is built on Modal), so
// the card is the part both of them own. Not exported from the barrel: callers want
// ConfirmModal, or a `dirty` Modal.
export function ConfirmCard({
  title,
  body,
  confirmLabel,
  cancelLabel,
  onCancel,
  onConfirm,
  pending = false,
}: {
  title: string;
  body: string;
  confirmLabel: string;
  cancelLabel: string;
  onCancel: () => void;
  onConfirm: () => void;
  pending?: boolean;
}) {
  return (
    <div className="p-6">
      <div className="mb-2 text-h3 font-medium">{title}</div>
      <div className="mb-6 text-body text-content-muted">{body}</div>
      <div className="flex justify-end gap-2">
        <Button onClick={onCancel}>{cancelLabel}</Button>
        <Button variant="danger" onClick={onConfirm} loading={pending}>
          {confirmLabel}
        </Button>
      </div>
    </div>
  );
}
