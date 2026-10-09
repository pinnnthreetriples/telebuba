import type { KeyboardEvent, MouseEvent, ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import type { AccountFolders } from '@/shared/api';
import { FOCUS_RING } from '@/shared/design-system';
import { cn } from '@/shared/lib';
import { Icon, IconButton } from '@/shared/ui';

import { ALL_VIEW, type FolderView, UNFILED_VIEW } from '../model/filters';
import { DROP_ATTRIBUTE } from '../model/useAccountDrag';

export const FOLDER_PANEL_ID = 'account-folder-panel';
export const ROW_CHECKBOX = 'size-spinner shrink-0 accent-action-primary disabled:opacity-40';

export interface SelectAllState {
  checked: boolean;
  indeterminate: boolean;
  disabled: boolean;
  onToggle: () => void;
}

interface Tab {
  id: FolderView;
  name: string;
  count: number;
  folder: boolean;
}

// The panel's top edge: a strip on the canvas tint whose active tab is the table
// header's colour and runs flush into it. Left of the tabs, select-all; right of them,
// the page toolbar (`actions`), then the folder controls.
export function FolderTabStrip({
  view,
  onView,
  folders,
  selectAll,
  dropFolderId,
  onCreate,
  onSettings,
  actions,
}: {
  view: FolderView;
  onView: (view: FolderView) => void;
  folders: AccountFolders | undefined;
  selectAll: SelectAllState;
  dropFolderId: string | null;
  onCreate: () => void;
  onSettings: (folderId: string) => void;
  actions: ReactNode;
}) {
  const { t } = useTranslation();
  const tabs: Tab[] = [
    {
      id: ALL_VIEW,
      name: t('accounts.folders.all'),
      count: folders?.total_count ?? 0,
      folder: false,
    },
    ...(folders?.items ?? []).map((folder) => ({
      id: folder.folder_id,
      name: folder.name,
      count: folder.account_count,
      folder: true,
    })),
    {
      id: UNFILED_VIEW,
      name: t('accounts.folders.unfiled'),
      count: folders?.unfiled_count ?? 0,
      folder: false,
    },
  ];
  const activeFolder = tabs.find((tab) => tab.id === view && tab.folder);

  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    const index = tabs.findIndex((tab) => tab.id === view);
    const next =
      event.key === 'ArrowRight'
        ? tabs[(index + 1) % tabs.length]
        : event.key === 'ArrowLeft'
          ? tabs[(index - 1 + tabs.length) % tabs.length]
          : undefined;
    if (!next) return;
    event.preventDefault();
    onView(next.id);
    document.getElementById(`account-folder-tab-${next.id}`)?.focus();
  };

  return (
    // `relative`: the filter card in `actions` hangs from this strip's right edge.
    <div className="relative flex flex-wrap items-end gap-1 rounded-t-lg border border-b-0 border-line bg-canvas px-1 pr-2 pt-1">
      <div className="order-last flex w-full min-w-0 items-end sm:order-none sm:w-auto sm:flex-1">
        <label className="mb-1 flex shrink-0 items-center self-center pl-4 pr-2">
          <input
            type="checkbox"
            aria-label={t('accounts.folders.selectAll')}
            checked={selectAll.checked}
            disabled={selectAll.disabled}
            ref={(element) => {
              if (element) element.indeterminate = selectAll.indeterminate;
            }}
            onChange={selectAll.onToggle}
            className={ROW_CHECKBOX}
          />
        </label>
        <div
          role="tablist"
          aria-label={t('accounts.folders.tabsLabel')}
          className="tb-scroll flex min-w-0 flex-1 items-end gap-1 overflow-x-auto"
        >
          {tabs.map((tab) => {
            const active = tab.id === view;
            const dropping = tab.folder && tab.id === dropFolderId;
            return (
              <button
                key={tab.id}
                id={`account-folder-tab-${tab.id}`}
                type="button"
                role="tab"
                aria-selected={active}
                aria-controls={FOLDER_PANEL_ID}
                tabIndex={active ? 0 : -1}
                {...(tab.folder ? { [DROP_ATTRIBUTE]: tab.id } : {})}
                onKeyDown={onKeyDown}
                onClick={() => {
                  onView(tab.id);
                }}
                onContextMenu={(event: MouseEvent) => {
                  if (!tab.folder) return;
                  event.preventDefault();
                  onSettings(tab.id);
                }}
                className={cn(
                  'shrink-0 whitespace-nowrap px-4 transition-colors',
                  FOCUS_RING,
                  active
                    ? 'rounded-t-md bg-surface pb-3 pt-2 type-body-medium text-content-primary'
                    : dropping
                      ? 'mb-1 rounded-md bg-info-tint py-2 type-body text-info-strong'
                      : 'mb-1 rounded-md py-2 type-body text-content-muted hover:text-content-primary',
                )}
              >
                {tab.name}
                {active ? <span className="ml-2 type-small tabular-nums">{tab.count}</span> : null}
              </button>
            );
          })}
        </div>
      </div>
      <div className="mb-1 ml-auto flex min-w-0 items-center gap-2 self-center">{actions}</div>
      <div className="mb-1 flex shrink-0 items-center gap-2 self-center border-l border-line pl-2">
        <IconButton
          size="md"
          tone="neutral"
          aria-label={t('accounts.folders.create')}
          title={t('accounts.folders.create')}
          onClick={onCreate}
        >
          <Icon name="folder-plus" size={16} />
        </IconButton>
        {activeFolder ? (
          <IconButton
            size="md"
            tone="neutral"
            aria-label={t('accounts.folders.settings', { name: activeFolder.name })}
            title={t('accounts.folders.settingsHint')}
            onClick={() => {
              onSettings(activeFolder.id);
            }}
          >
            <Icon name="gear" size={16} />
          </IconButton>
        ) : (
          // Holds the gear's place on «Все» and «Без папки», so the toolbar to its
          // left does not jump sideways when a folder tab is opened.
          <span aria-hidden className="size-icon" />
        )}
      </div>
    </div>
  );
}
