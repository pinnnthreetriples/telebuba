import { createPortal } from 'react-dom';

import { AccountAvatar, accountAvatarTint } from '@/entities/account';
import type { AccountRead } from '@/shared/api';
import { cn } from '@/shared/lib';
import { Icon } from '@/shared/ui';

import type { AccountDrag } from '../model/useAccountDrag';

const SHOWN = 4;
// The ghost sits just below-right of the pointer so it never covers the tab it targets.
const OFFSET_PX = 12;

// What a drag carries: up to four overlapping avatars and «+N», and — over a folder
// tab — the folder it will land in. Portalled and `fixed`, for the reason the Toaster
// is: a transformed ancestor would otherwise become its containing block.
export function DragGhost({
  drag,
  accounts,
  folderName,
}: {
  drag: AccountDrag;
  accounts: readonly AccountRead[];
  folderName: string | null;
}) {
  const shown = drag.ids
    .slice(0, SHOWN)
    .map((id) => accounts.find((account) => account.account_id === id))
    .filter((account): account is AccountRead => account !== undefined);
  const rest = drag.ids.length - SHOWN;
  return createPortal(
    <div
      data-testid="drag-ghost"
      className="pointer-events-none fixed z-toast"
      style={{ left: drag.x + OFFSET_PX, top: drag.y + OFFSET_PX }}
    >
      <div
        className={cn(
          'flex items-center gap-2 rounded-full border bg-surface-card py-1 pl-1 shadow-pop',
          folderName ? 'border-info-line pr-3' : 'border-line pr-1',
        )}
      >
        <span className="flex items-center">
          {shown.map((account, index) => (
            <span
              key={account.account_id}
              className={cn('rounded-full border-2 border-surface-card', index > 0 && '-ml-2')}
            >
              <AccountAvatar
                account={account}
                className="size-icon rounded-full"
                fallbackClassName={`text-small font-medium ${accountAvatarTint(account.status)}`}
              />
            </span>
          ))}
          {rest > 0 ? (
            <span className="-ml-2 flex size-icon items-center justify-center rounded-full border-2 border-surface-card bg-canvas text-small font-medium text-content-secondary tabular-nums">
              +{rest}
            </span>
          ) : null}
        </span>
        {folderName ? (
          <span className="flex items-center gap-1 whitespace-nowrap type-small-medium text-action-primary">
            <Icon name="folder-plus" size={14} />
            {folderName}
          </span>
        ) : null}
      </div>
    </div>,
    document.body,
  );
}
