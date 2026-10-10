import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { AccountFolder } from '@/shared/api';
import { Icon } from '@/shared/ui';

// Under a row's @username: a folder glyph and how many folders the account is in, the
// names in the tooltip. A click unfolds the names as links that open each folder.
// Accounts in no folder show nothing.
export function FolderTag({
  folderIds,
  folders,
  onOpenFolder,
}: {
  folderIds: readonly string[];
  folders: readonly AccountFolder[];
  onOpenFolder: (folderId: string) => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const named = folderIds
    .map((id) => folders.find((folder) => folder.folder_id === id))
    .filter((folder): folder is AccountFolder => folder !== undefined);
  if (named.length === 0) return null;
  const names = named.map((folder) => folder.name).join(', ');
  return (
    // Its clicks are its own: neither the count nor a folder link opens the row.
    <span
      className="mt-1 flex flex-wrap items-center gap-2"
      onClick={(event) => {
        event.stopPropagation();
      }}
    >
      <button
        type="button"
        className="inline-flex items-center gap-1 type-small text-action-primary hover:text-action-pressed"
        title={names}
        aria-label={t('accounts.folders.tag', { names })}
        aria-expanded={open}
        onClick={() => {
          setOpen((value) => !value);
        }}
      >
        <Icon name="folder" size={14} />
        <span className="tabular-nums">{named.length}</span>
      </button>
      {open
        ? named.map((folder) => (
            <button
              key={folder.folder_id}
              type="button"
              className="type-small text-action-primary hover:text-action-pressed"
              onClick={() => {
                onOpenFolder(folder.folder_id);
              }}
            >
              {folder.name}
            </button>
          ))
        : null}
    </span>
  );
}
