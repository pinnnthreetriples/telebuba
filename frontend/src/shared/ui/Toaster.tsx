import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';

import { surface } from '@/shared/design-system';
import { cn } from '@/shared/lib/cn';

import { Icon } from './Icon';
import { dismissToast, getToasts, subscribe, type Toast } from './toast';

// Renders the toast queue (see toast.ts). Mounted once at the app root; styling
// matches the design's dark tooltip (#16161A).
//
// Portalled into document.body rather than rendered in place: the stack is `fixed`,
// and a `fixed` element inside an ancestor that has a transform or a filter is
// positioned against that ancestor instead of the viewport. Nothing on the page
// does that today, but a page-level animation is one class away from it, and a
// toast that lands in the middle of a card rather than at the bottom of the screen
// is a hard thing to trace back to a class on an ancestor.
//
// The stack sits on its own `z-toast` rung, one above `z-dialog`: a toast reports
// the outcome of an action, and the dialog that action was taken in is usually
// still open behind it.
export function Toaster() {
  const [items, setItems] = useState<Toast[]>(getToasts);
  useEffect(() => subscribe(setItems), []);

  if (items.length === 0) return null;
  return createPortal(
    <div className="pointer-events-none fixed bottom-6 left-1/2 z-toast flex -translate-x-1/2 flex-col items-center gap-3">
      {items.map((toast) =>
        toast.tone === 'error' ? (
          <div
            key={toast.id}
            role="alert"
            className={cn(
              'pointer-events-auto max-w-[90vw] px-4 py-3 text-body text-on-fill shadow-pop tb-arrive',
              surface('inverse'),
            )}
          >
            {toast.message}
          </div>
        ) : (
          <SuccessToast key={toast.id} toast={toast} />
        ),
      )}
    </div>,
    document.body,
  );
}

// A finished action: check mark, the outcome, an optional undo and a close button.
function SuccessToast({ toast }: { toast: Toast }) {
  const { t } = useTranslation();
  return (
    <div
      role="status"
      className={cn(
        'pointer-events-auto flex max-w-[90vw] items-center gap-3 py-2 pl-4 pr-2 text-on-fill shadow-pop tb-arrive',
        surface('inverse'),
      )}
    >
      <Icon name="check" size={16} className="shrink-0 text-success" />
      <span className="text-body text-on-fill">{toast.message}</span>
      {toast.action ? (
        <button
          type="button"
          className="rounded-md px-2 py-1 text-body font-medium text-info-line hover:text-on-fill"
          onClick={() => {
            toast.action?.onClick();
            dismissToast(toast.id);
          }}
        >
          {toast.action.label}
        </button>
      ) : null}
      <button
        type="button"
        aria-label={t('shell.toastClose')}
        className="flex size-icon shrink-0 items-center justify-center rounded-md opacity-60 hover:opacity-100"
        onClick={() => {
          dismissToast(toast.id);
        }}
      >
        <Icon name="close" size={14} />
      </button>
    </div>
  );
}
