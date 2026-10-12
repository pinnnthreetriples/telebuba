import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';

import {
  liftWarmingDmPauseMutation,
  warmingBoardQueryOptions,
  warmingSettingsQueryOptions,
} from '@/entities/warming';
import { Button, Notice } from '@/shared/ui';

// The fleet DM breaker: two DM spam blocks within a day pause DMs for every account.
// The backend sends `dm_paused_until` only while the pause holds, so the banner shows
// exactly then, with the instant DMs reopen and a button that lifts the pause early.
export function DmPauseBanner({ pausedUntil }: { pausedUntil?: string | null }) {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const lift = useMutation(liftWarmingDmPauseMutation());
  const format = useMemo(
    () =>
      new Intl.DateTimeFormat(i18n.language, {
        day: 'numeric',
        month: 'short',
        hour: '2-digit',
        minute: '2-digit',
      }),
    [i18n.language],
  );
  const until = pausedUntil ? new Date(pausedUntil) : null;
  if (!until || Number.isNaN(until.getTime())) return null;

  const onLift = () => {
    lift.mutate(
      {},
      {
        onSettled: () => {
          void queryClient.invalidateQueries({ queryKey: warmingBoardQueryOptions().queryKey });
          void queryClient.invalidateQueries({
            queryKey: warmingSettingsQueryOptions().queryKey,
          });
        },
      },
    );
  };

  return (
    <div className="mb-4">
      <Notice
        tone="warning"
        role="status"
        className="flex flex-wrap items-center justify-between gap-3"
      >
        <div className="min-w-0">
          <p className="m-0 type-body-medium">
            {t('warming.dmPause.banner', { date: format.format(until) })}
          </p>
          <p className="m-0 type-small">{t('warming.dmPause.detail')}</p>
        </div>
        <Button size="sm" loading={lift.isPending} onClick={onLift}>
          {t('warming.dmPause.lift')}
        </Button>
      </Notice>
    </div>
  );
}
