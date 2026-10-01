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
    <Notice
      tone="danger"
      contentGap="row"
      className="flex items-center justify-between"
      role={role}
    >
      <span>{message}</span>
      <Button
        size="xs"
        variant="dangerSurface"

        disabled={disabled}
        onClick={onRetry}
      >
        {label}
      </Button>
    </Notice>
  );
}
