import type { ReactNode } from 'react';

import { Button, Notice } from '@/shared/ui';

export function RetryNotice({
  message,
  label,
  onRetry,
  disabled = false,
  role,
}: {
  message: ReactNode;
  label: string;
  onRetry: () => void;
  disabled?: boolean;
  role?: 'alert';
}) {
  return (
    <Notice tone="danger" className="flex items-center justify-between gap-md" role={role}>
      <span>{message}</span>
      <Button
        size="xs"
        variant="danger"
        className="bg-surface-card"
        disabled={disabled}
        onClick={onRetry}
      >
        {label}
      </Button>
    </Notice>
  );
}
