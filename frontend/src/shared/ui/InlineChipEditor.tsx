import { cn } from '@/shared/lib/cn';

import { Icon } from './Icon';
import { IconButton } from './IconButton';

export function InlineChipEditor({
  value,
  onChange,
  onConfirm,
  onCancel,
  placeholder,
  inputLabel,
  confirmLabel,
  cancelLabel,
  disabled = false,
  className,
}: {
  value: string;
  onChange: (value: string) => void;
  onConfirm: () => void;
  onCancel: () => void;
  placeholder: string;
  inputLabel: string;
  confirmLabel: string;
  cancelLabel?: string;
  disabled?: boolean;
  className?: string;
}) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-tight rounded-full border border-action-primary bg-surface-card py-xs pl-md pr-xs',
        className,
      )}
    >
      <input
        autoFocus
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter' && value.trim() && !disabled) onConfirm();
          if (event.key === 'Escape') onCancel();
        }}
        placeholder={placeholder}
        aria-label={inputLabel}
        className="w-col border-none bg-transparent text-body outline-none"
      />
      <IconButton
        size="sm"
        shape="circle"
        tone="action"
        className="border-transparent"
        title={confirmLabel}
        aria-label={confirmLabel}
        disabled={disabled || !value.trim()}
        onClick={onConfirm}
      >
        <Icon name="check" size={12} />
      </IconButton>
      {cancelLabel ? (
        <IconButton
          size="sm"
          shape="circle"
          title={cancelLabel}
          aria-label={cancelLabel}
          onClick={onCancel}
          className="bg-canvas text-content-muted"
        >
          <Icon name="close" size={16} />
        </IconButton>
      ) : null}
    </span>
  );
}
