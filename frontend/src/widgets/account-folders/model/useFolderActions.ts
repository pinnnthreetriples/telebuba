import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';

import {
  addAccountsToFolderMutation,
  createAccountFolderMutation,
  deleteAccountFolderMutation,
  fetchFolderMemberIds,
  invalidateAccountFolderViews,
  removeAccountsFromFolderMutation,
  renameAccountFolderMutation,
} from '@/entities/account';
import type { AccountFolder } from '@/shared/api';
import { mutationErrorText } from '@/shared/lib';
import { toastError, toastSuccess } from '@/shared/ui';

// Every folder write, each ending in a toast with an exact undo. mutateAsync throughout:
// two drops can be in flight at once, and one useMutation is one callback slot. A failed
// write is reported by the global mutation toast; the promise still rejects so a dialog
// can stay open over it.
export function useFolderActions(folders: readonly AccountFolder[]) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const create = useMutation(createAccountFolderMutation());
  const rename = useMutation(renameAccountFolderMutation());
  const remove = useMutation(deleteAccountFolderMutation());
  const add = useMutation(addAccountsToFolderMutation());
  const takeOut = useMutation(removeAccountsFromFolderMutation());

  const refresh = () => {
    invalidateAccountFolderViews(queryClient);
  };
  const nameOf = (folderId: string) =>
    folders.find((folder) => folder.folder_id === folderId)?.name ?? '';

  const announce = (message: string, undo: () => Promise<unknown>) => {
    toastSuccess(message, {
      label: t('accounts.folders.undo'),
      onClick: () => {
        void undo()
          .then(() => toastSuccess(t('accounts.folders.undone')))
          .catch(() => undefined)
          .finally(refresh);
      },
    });
  };

  const addAccounts = async (folderId: string, accountIds: readonly string[]) => {
    const folder = nameOf(folderId);
    const change = await add
      .mutateAsync({ path: { folder_id: folderId }, body: { account_ids: [...accountIds] } })
      .finally(refresh);
    const changed = change.account_ids;
    if (changed.length === 0) {
      toastSuccess(t('accounts.folders.alreadyIn'));
      return;
    }
    announce(t('accounts.folders.added', { count: changed.length, folder }), () =>
      takeOut.mutateAsync({ path: { folder_id: folderId }, body: { account_ids: changed } }),
    );
  };

  const createFolder = async (name: string) => {
    const folder = await create.mutateAsync({ body: { name } }).finally(refresh);
    announce(t('accounts.folders.created', { name: folder.name }), () =>
      remove.mutateAsync({ path: { folder_id: folder.folder_id } }),
    );
    return folder;
  };

  const renameFolder = async (folderId: string, name: string) => {
    const before = nameOf(folderId);
    await rename.mutateAsync({ path: { folder_id: folderId }, body: { name } }).finally(refresh);
    announce(t('accounts.folders.renamed'), () =>
      rename.mutateAsync({ path: { folder_id: folderId }, body: { name: before } }),
    );
  };

  // Deleting forgets the memberships, so they are read first: the undo recreates the
  // folder under its name and puts the same accounts back.
  const deleteFolder = async (folderId: string) => {
    const name = nameOf(folderId);
    let members: string[];
    try {
      members = await fetchFolderMemberIds(folderId);
    } catch (error) {
      toastError(mutationErrorText(error));
      throw error;
    }
    await remove.mutateAsync({ path: { folder_id: folderId } }).finally(refresh);
    announce(t('accounts.folders.deleted'), async () => {
      const restored = await create.mutateAsync({ body: { name } });
      if (members.length > 0) {
        await add.mutateAsync({
          path: { folder_id: restored.folder_id },
          body: { account_ids: members },
        });
      }
    });
  };

  return { addAccounts, createFolder, renameFolder, deleteFolder };
}

export type FolderActions = ReturnType<typeof useFolderActions>;
