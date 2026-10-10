// Account folders: operator-named lists of accounts (many-to-many), plus the filter
// options the Accounts page builds its pills from. Wraps the generated client so pages
// and widgets depend on the entity, not on the generated internals.
import type { QueryClient } from '@tanstack/react-query';

import { listAccounts } from '@/shared/api';
import {
  accountFilterOptionsQueryKey,
  listAccountFoldersQueryKey,
  listAccountsQueryKey,
} from '@/shared/api/@tanstack/react-query.gen';

// The backend caps a page at 200 (api/v1/accounts.py).
const MEMBERS_PAGE_SIZE = 200;

// A folder write changes the folder tabs, which rows the list shows and the counts the
// filter pills carry — all three, and nothing else.
export function invalidateAccountFolderViews(queryClient: QueryClient): void {
  void queryClient.invalidateQueries({ queryKey: listAccountFoldersQueryKey() });
  void queryClient.invalidateQueries({ queryKey: listAccountsQueryKey() });
  void queryClient.invalidateQueries({ queryKey: accountFilterOptionsQueryKey() });
}

// Every account id in one folder, across pages. Deleting a folder forgets its members,
// so this is what makes the delete undoable.
export async function fetchFolderMemberIds(folderId: string): Promise<string[]> {
  const ids: string[] = [];
  const seen = new Set<string>();
  let cursor: string | null | undefined;
  do {
    if (cursor != null) {
      if (seen.has(cursor)) break;
      seen.add(cursor);
    }
    const { data } = await listAccounts({
      query: { folder: folderId, cursor: cursor ?? undefined, limit: MEMBERS_PAGE_SIZE },
      throwOnError: true,
    });
    ids.push(...data.items.map((account) => account.account_id));
    cursor = data.next_cursor;
  } while (cursor);
  return ids;
}

export {
  accountFilterOptionsOptions as accountFilterOptionsQueryOptions,
  addAccountsToFolderMutation,
  createAccountFolderMutation,
  deleteAccountFolderMutation,
  listAccountFoldersOptions as accountFoldersQueryOptions,
  removeAccountsFromFolderMutation,
  renameAccountFolderMutation,
} from '@/shared/api/@tanstack/react-query.gen';
