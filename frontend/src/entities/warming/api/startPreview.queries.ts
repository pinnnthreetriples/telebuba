// When an operator-chosen start delay lands in each account's own clock. The
// endpoint is a read behind POST (the pool sends every idle id), so it is wrapped
// as a query here: the start dialog re-reads it whenever the delay changes.
import { queryOptions } from '@tanstack/react-query';

import { previewWarmingStart, type StartPreviewRequest } from '@/shared/api';

export function warmingStartPreviewQueryOptions(body: StartPreviewRequest) {
  return queryOptions({
    queryKey: ['warmingStartPreview', body] as const,
    queryFn: async ({ signal }) => {
      const { data } = await previewWarmingStart({ body, throwOnError: true, signal });
      return data;
    },
  });
}
