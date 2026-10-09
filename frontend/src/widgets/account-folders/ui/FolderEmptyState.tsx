import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { Button } from '@/shared/ui';

// The bottom of the folder panel when it holds no table: square on top, where the tab
// strip sits on it.
export function PanelFloor({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-b-lg border border-line bg-surface-card px-4 py-4">{children}</div>
  );
}

// The panel's floor when the open view has no rows: an empty folder, or filters (and
// search) that match nothing in it.
export function FolderEmptyState({ onResetFilters }: { onResetFilters: (() => void) | null }) {
  const { t } = useTranslation();
  return (
    <PanelFloor>
      <div className="flex flex-wrap items-center gap-3">
        <span className="type-body-medium">
          {onResetFilters ? t('accounts.folders.nothingFound') : t('accounts.folders.emptyFolder')}
        </span>
        {onResetFilters ? (
          <Button size="sm" onClick={onResetFilters}>
            {t('accounts.folders.resetFilters')}
          </Button>
        ) : null}
      </div>
    </PanelFloor>
  );
}
