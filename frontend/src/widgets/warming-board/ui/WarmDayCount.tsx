import { useTranslation } from 'react-i18next';

// "5 / 7 дней (2 дня простоя не засчитаны)": days that counted toward the target
// (a worked cycle or a planned quiet day), then the stint's days that did not —
// error, quarantine — so the operator sees why the count trails the calendar.
export function WarmDayCount({
  days,
  target,
  idle,
}: {
  days: number;
  target: number;
  idle: number;
}) {
  const { t } = useTranslation();
  return (
    <span className="text-right">
      <span className="type-small-medium tabular-nums text-content-primary">
        {t('warming.card.dayProgress', { days, target, count: target })}
      </span>
      {idle > 0 ? (
        <span className="ml-1 type-small text-content-subtle">
          {t('warming.card.idleDays', { count: idle })}
        </span>
      ) : null}
    </span>
  );
}
