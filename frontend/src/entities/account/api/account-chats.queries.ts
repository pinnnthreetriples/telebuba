import { infiniteQueryOptions } from '@tanstack/react-query';

import { listAccountChatMessages, listAccountChats, type ChatDialog } from '@/shared/api';
import {
  listAccountChatsInfiniteQueryKey,
  listAccountChatMessagesQueryKey,
} from '@/shared/api/@tanstack/react-query.gen';

const CHAT_PAGE_SIZE = 50;
export type AccountChatPeerType = ChatDialog['peer_type'];

export function accountChatsQueryKey(accountId: string) {
  return listAccountChatsInfiniteQueryKey({
    path: { account_id: accountId },
    query: { limit: CHAT_PAGE_SIZE },
  });
}

export function accountChatsInfiniteQueryOptions(accountId: string) {
  const options = { path: { account_id: accountId }, query: { limit: CHAT_PAGE_SIZE } };
  return infiniteQueryOptions({
    queryKey: listAccountChatsInfiniteQueryKey(options),
    initialPageParam: undefined as string | undefined,
    queryFn: async ({ pageParam, signal }) => {
      const { data } = await listAccountChats({
        ...options,
        query: { limit: CHAT_PAGE_SIZE, cursor: pageParam },
        signal,
        throwOnError: true,
      });
      return data;
    },
    getNextPageParam: (lastPage: { next_cursor?: string | null }) =>
      lastPage.next_cursor ?? undefined,
  });
}

export function accountChatHistoryQueryKey(
  accountId: string,
  peerType: AccountChatPeerType,
  peerId: string,
) {
  return listAccountChatMessagesQueryKey({
    path: { account_id: accountId, peer_type: peerType, peer_id: peerId },
    query: { limit: CHAT_PAGE_SIZE },
  });
}

export function accountChatHistoryInfiniteQueryOptions(
  accountId: string,
  peerType: AccountChatPeerType,
  peerId: string,
) {
  const path = { account_id: accountId, peer_type: peerType, peer_id: peerId };
  return infiniteQueryOptions({
    queryKey: accountChatHistoryQueryKey(accountId, peerType, peerId),
    initialPageParam: undefined as number | undefined,
    queryFn: async ({ pageParam, signal }) => {
      const { data } = await listAccountChatMessages({
        path,
        query: { limit: CHAT_PAGE_SIZE, before_id: pageParam },
        signal,
        throwOnError: true,
      });
      return data;
    },
    getNextPageParam: (lastPage) => lastPage.next_before_id ?? undefined,
  });
}

export type AccountChatDialog = ChatDialog;
