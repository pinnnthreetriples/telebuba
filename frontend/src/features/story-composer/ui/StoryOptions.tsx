import { useTranslation } from 'react-i18next';

import { Badge, SegmentedControl, SettingRow, Switch } from '@/shared/ui';

import { DEFAULT_PERIOD_HOURS } from '../model/limits';
import type { StoryDesign, StoryPeriodHours, StoryPrivacyPreset } from '../model/types';

const PERIODS: readonly StoryPeriodHours[] = [6, 12, 24, 48];
const AUDIENCES: readonly StoryPrivacyPreset[] = ['contacts', 'close_friends', 'public'];

type OptionsFields = Pick<
  StoryDesign,
  'period_hours' | 'pinned' | 'protect_content' | 'privacy_preset'
>;

export function StoryOptions({
  design,
  onChange,
  premium,
  disabled = false,
}: {
  design: OptionsFields;
  onChange: (patch: Partial<OptionsFields>) => void;
  premium: boolean | null;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex flex-col gap-3">
      <SettingRow label={t('storyComposer.options.period')} first>
        <SegmentedControl
          value={String(design.period_hours)}
          disabled={disabled}
          ariaLabel={t('storyComposer.options.period')}
          options={PERIODS.map((hours) => ({
            value: String(hours),
            label: (
              <span className="inline-flex items-center justify-center gap-1">
                {t('storyComposer.options.periodHours', { count: hours })}
                {hours !== DEFAULT_PERIOD_HOURS && premium !== true ? (
                  <Badge tone="info">{t('storyComposer.options.premium')}</Badge>
                ) : null}
              </span>
            ),
            ariaLabel: t('storyComposer.options.periodHours', { count: hours }),
          }))}
          onChange={(value) => {
            onChange({ period_hours: Number(value) as StoryPeriodHours });
          }}
        />
      </SettingRow>
      <SettingRow label={t('storyComposer.options.pinned')}>
        <Switch
          label={t('storyComposer.options.pinned')}
          checked={design.pinned}
          disabled={disabled}
          onChange={(pinned) => {
            onChange({ pinned });
          }}
        />
      </SettingRow>
      <SettingRow label={t('storyComposer.options.protect')}>
        <Switch
          label={t('storyComposer.options.protect')}
          checked={design.protect_content}
          disabled={disabled}
          onChange={(protect_content) => {
            onChange({ protect_content });
          }}
        />
      </SettingRow>
      <SettingRow label={t('storyComposer.options.audience')}>
        <SegmentedControl
          value={design.privacy_preset}
          disabled={disabled}
          ariaLabel={t('storyComposer.options.audience')}
          options={AUDIENCES.map((preset) => ({
            value: preset,
            label: t(`storyComposer.options.audiences.${preset}`),
          }))}
          onChange={(privacy_preset) => {
            onChange({ privacy_preset });
          }}
        />
      </SettingRow>
    </div>
  );
}
