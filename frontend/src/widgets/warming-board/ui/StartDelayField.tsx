import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';

import { warmingStartPreviewQueryOptions } from '@/entities/warming';
import { SegmentedControl } from '@/shared/ui';

import { groupStartPreview } from '../lib/startPreview';

import { RangeSlider } from './RangeSlider';

const DELAY_MIN = 1;
const DELAY_MAX = 24;
const DELAY_PRESETS = [1, 3, 6, 12, 24];

// "Первый заход через": how long the account rests before its first cycle, and when
// that lands in the account's own clock. The night-to-morning rule lives on the
// backend, so the preview asks it rather than redoing the rule here. A pool start
// (`spread`) wakes each account at its own moment inside the window, so it reads as a
// range.
export function StartDelayField({
  hours,
  accountIds,
  spread,
  onChange,
}: {
  hours: number;
  accountIds: string[];
  spread: boolean;
  onChange: (hours: number) => void;
}) {
  const { t, i18n } = useTranslation();
  const preview = useQuery({
    ...warmingStartPreviewQueryOptions({
      account_ids: accountIds,
      start_delay_hours: hours,
      spread_start: spread,
    }),
    // Dragging the slider re-asks; the last answer stays on screen meanwhile.
    placeholderData: keepPreviousData,
  });
  const zones = groupStartPreview(preview.data?.items ?? [], i18n.language);
  const shifted = zones.some((zone) => zone.shifted);
  const hourCount = (n: number) => t('warming.delay.hours', { count: n });
  const lines = zones.map((zone) =>
    zones.length > 1
      ? t('warming.delay.rangeZone', { from: zone.from, to: zone.to, zone: zone.timeZone })
      : zone.from === zone.to
        ? t(spread ? 'warming.delay.atPool' : 'warming.delay.at', { time: zone.from })
        : t('warming.delay.range', { from: zone.from, to: zone.to }),
  );
  const note = t(spread ? 'warming.delay.shiftedPool' : 'warming.delay.shifted');

  return (
    <div className="mb-6">
      <div className="mb-2 type-body-medium">{t('warming.delay.label')}</div>
      <SegmentedControl
        variant="outline"
        className="mb-4"
        ariaLabel={t('warming.delay.label')}
        value={String(hours)}
        options={DELAY_PRESETS.map((n) => ({
          value: String(n),
          label: t('warming.delay.short', { count: n }),
        }))}
        onChange={(value) => {
          onChange(Number(value));
        }}
      />
      <RangeSlider
        value={hours}
        min={DELAY_MIN}
        max={DELAY_MAX}
        ticks={DELAY_PRESETS}
        label={t('warming.delay.label')}
        valueText={hourCount}
        onChange={onChange}
      />
      {/* Polite: the line re-reads as the slider moves, never interrupting. */}
      <div aria-live="polite" className="mt-2 type-small text-content-muted">
        {lines.map((line) => (
          <div key={line}>
            {line}
            {/* One zone carries the night note inline; a mixed pool gets its own line. */}
            {shifted && lines.length === 1 ? ` ${note}` : null}
          </div>
        ))}
        {shifted && lines.length > 1 ? <div>{note}</div> : null}
      </div>
    </div>
  );
}
