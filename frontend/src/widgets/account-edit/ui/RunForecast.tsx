import { useTranslation } from 'react-i18next';

import { Odometer } from '@/shared/ui';

import type { SendForecast } from '../model/pacing';

// The forecast line under the pace sliders: how many messages, and either how long
// they take today or over how many working days the caps spread them.
export function RunForecast({ forecast }: { forecast: SendForecast }) {
  const { t } = useTranslation();
  if (forecast.sends === 0) return <>{t('accounts.messages.pacing.forecastEmpty')}</>;
  const minutes = Math.round(forecast.seconds / 60);
  const duration =
    forecast.days > 1
      ? t('accounts.messages.pacing.duration.days', { count: forecast.days })
      : minutes < 1
        ? t('accounts.messages.pacing.duration.underMinute')
        : minutes < 60
          ? t('accounts.messages.pacing.duration.m', { m: minutes })
          : t('accounts.messages.pacing.duration.hm', {
              h: Math.floor(minutes / 60),
              m: minutes % 60,
            });
  return (
    <>
      <span className="inline-flex items-baseline gap-1">
        <Odometer value={forecast.sends} className="type-small-medium" />
        {t('accounts.messages.pacing.forecastSends', { count: forecast.sends, duration })}
      </span>
      {forecast.days > 1 && (
        <span className="block">{t('accounts.messages.pacing.forecastSpread')}</span>
      )}
    </>
  );
}
