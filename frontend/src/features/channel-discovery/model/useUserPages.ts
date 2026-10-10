import { useInfiniteQuery } from '@tanstack/react-query';

import { userParserBaseUsersInfiniteOptions } from '@/entities/user-parser';

const PAGE = 100;

// Люди базы страницами по сотне: «Показать ещё» дозагружает следующую. Поиск — серверный,
// поэтому большая база не приезжает в окно целиком.
export function useUserPages(runId: string | null, search = '', enabled = true) {
  const query = useInfiniteQuery({
    ...userParserBaseUsersInfiniteOptions({
      path: { run_id: runId ?? '' },
      query: { search, limit: PAGE },
    }),
    initialPageParam: 0,
    getNextPageParam: (last, pages) => {
      const loaded = pages.reduce((sum, page) => sum + page.items.length, 0);
      return loaded < last.total ? loaded : undefined;
    },
    enabled: enabled && runId !== null,
  });
  return {
    users: query.data?.pages.flatMap((page) => page.items) ?? [],
    total: query.data?.pages[0]?.total ?? 0,
    hasMore: query.hasNextPage,
    loadingMore: query.isFetchingNextPage,
    loading: query.isPending && enabled && runId !== null,
    more: () => {
      void query.fetchNextPage();
    },
  };
}
