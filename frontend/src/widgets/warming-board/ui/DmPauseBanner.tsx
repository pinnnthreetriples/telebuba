import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  liftWarmingDmPauseMutation,
  warmingBoardQueryOptions,
  warmingSettingsQueryOptions,
} from '@/entities/warming';
import { Button, ConfirmModal, Notice } from '@/shared/ui';

// The fleet DM breaker: two DM spam blocks within a day pause DMs for every account.
// The backend sends `dm_paused_until` only while the pause holds, so the banner shows
// exactly then, with the instant DMs reopen and a button that lifts the pause early.
// Lifting reopens DMs for the whole fleet, so it asks first; the backend logs who did it.
export function DmPauseBanner({ pausedUntil }: { pausedUntil?: string | null }) {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const lift = useMutation(liftWarmingDmPauseMutation());
  const [confirming, setConfirming] = useState(false);
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

  // A promise, so the dialog stays open (and pending) until the lift settles.
  const onLift = () =>
    lift.mutateAsync({}).finally(() => {
      void queryClient.invalidateQueries({ queryKey: warmingBoardQueryOptions().queryKey });
      void queryClient.invalidateQueries({ queryKey: warmingSettingsQueryOptions().queryKey });
    });

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
        <Button
          size="sm"
          loading={lift.isPending}
          onClick={() => {
            setConfirming(true);
          }}
        >
          {t('warming.dmPause.lift')}
        </Button>
      </Notice>
      {confirming ? (
        <ConfirmModal
          title={t('warming.dmPause.confirmTitle')}
          body={t('warming.dmPause.confirmBody', { date: format.format(until) })}
          confirmLabel={t('warming.dmPause.lift')}
          cancelLabel={t('warming.dmPause.cancel')}
          onClose={() => {
            setConfirming(false);
          }}
          onConfirm={onLift}
        />
      ) : null}
    </div>
  );
}
