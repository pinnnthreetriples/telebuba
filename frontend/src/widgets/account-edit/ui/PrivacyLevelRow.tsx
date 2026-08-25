import { useTranslation } from 'react-i18next';

import { SegmentedControl } from '@/shared/ui';

// The presentational half of the privacy tab: one Telegram privacy key's live
// level plus its three-way choice. Split out of PrivacyTab to keep that file
// focused on the queries. Internal to the slice (not re-exported from index).
const PRIVACY_LEVELS = ['everybody', 'contacts', 'nobody'] as const;

export type PrivacyLevel = (typeof PRIVACY_LEVELS)[number];
// What a READ can report: Telegram may hold a rule the dashboard does not model.
export type PrivacyShown = PrivacyLevel | 'unknown';

// The choice is a real radiogroup now — one tab stop and arrow keys — which is what
// this file used to refuse it on the grounds that the app could not implement it.
// `SegmentedControl` does, and it takes an 'unknown' level that matches no option: it
// presses nothing and still keeps the group reachable, which is the case the old
// aria-pressed idiom was chosen for.
export function PrivacyLevelRow({
  label,
  current,
  busy,
  onPick,
}: {
  label: string;
  current: PrivacyShown;
  busy: boolean;
  onPick: (level: PrivacyLevel) => void;
}) {
  const { t } = useTranslation();
  return (
    <div
      role="group"
      aria-label={label}
      className="flex items-center gap-md rounded-lg border border-line px-lg py-md"
    >
      <div className="min-w-0 flex-1">
        <div className="truncate type-card-title">{label}</div>
        <div className="mt-hair type-caption">
          {t('accounts.profile.privacy.current', {
            value: t(`accounts.profile.privacy.level.${current}`),
          })}
        </div>
        {current === 'unknown' && (
          <div className="mt-hair type-caption">{t('accounts.profile.privacy.unknownNote')}</div>
        )}
      </div>
      <SegmentedControl
        variant="outline"
        className="shrink-0"
        value={current}
        disabled={busy}
        ariaLabel={label}
        options={PRIVACY_LEVELS.map((level) => ({
          value: level,
          label: t(`accounts.profile.privacy.level.${level}`),
          // The visible text is only «Все»/«Контакты»/«Никто», so all three
          // rows together expose nine buttons with identical names in an
          // element list. The row goes into the accessible name.
          ariaLabel: `${label}: ${t(`accounts.profile.privacy.level.${level}`)}`,
        }))}
        onChange={(level) => {
          onPick(level);
        }}
      />
    </div>
  );
}
