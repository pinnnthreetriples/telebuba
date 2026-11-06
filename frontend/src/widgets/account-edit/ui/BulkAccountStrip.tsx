import { useTranslation } from 'react-i18next';

import { AccountAvatar } from '@/entities/account';
import type { AccountRead } from '@/shared/api';
import { Icon, IconButton } from '@/shared/ui';

// The batch strip: the add control and one avatar per selected account, each with a
// remove button while the batch is still being put together.
export function BulkAccountStrip({
  picked,
  locked,
  label,
  onAdd,
  onRemove,
}: {
  picked: (Partial<AccountRead> & { account_id: string })[];
  locked: boolean;
  label: (accountId: string) => string;
  onAdd: () => void;
  onRemove: (accountId: string) => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex items-center gap-3 border-b border-canvas px-6 py-3">
      <IconButton size="sm" disabled={locked} onClick={onAdd} aria-label={t('accounts.bulk.add')}>
        <Icon name="plus" size={16} />
      </IconButton>
      <div className="tb-scroll flex flex-1 items-center gap-2 overflow-x-auto py-1">
        {picked.map((row) => (
          <span key={row.account_id} className="group relative shrink-0">
            <AccountAvatar
              account={row}
              className="size-tile rounded-full"
              fallbackClassName="bg-canvas text-content-muted type-body-medium"
            />
            {!locked && picked.length > 1 && (
              <IconButton
                size="sm"
                shape="circle"
                aria-label={t('accounts.bulk.remove', { name: label(row.account_id) })}
                onClick={() => {
                  onRemove(row.account_id);
                }}
                className="absolute -right-1 -top-1 bg-surface-card opacity-0 transition-opacity focus-visible:opacity-100 group-hover:opacity-100"
              >
                <Icon name="close" size={16} />
              </IconButton>
            )}
          </span>
        ))}
      </div>
    </div>
  );
}
