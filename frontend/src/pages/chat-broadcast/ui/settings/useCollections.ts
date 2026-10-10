// The saved chat categories and the one way every write refreshes them.
import { useQuery, useQueryClient } from '@tanstack/react-query';

import { chatCollectionsQueryOptions } from '@/entities/chat-broadcast';
import type { ChatCollection } from '@/shared/api';

export function useCollections(): { items: ChatCollection[]; refresh: () => Promise<void> } {
  const queryClient = useQueryClient();
  const query = useQuery(chatCollectionsQueryOptions());
  return {
    items: query.data?.items ?? [],
    refresh: () =>
      queryClient.invalidateQueries({ queryKey: chatCollectionsQueryOptions().queryKey }),
  };
}
