import { useTranslation } from 'react-i18next';

// The presentational half of the privacy tab: one Telegram privacy key's live
// level plus its three-way choice. Split out of PrivacyTab to keep that file
// focused on the queries. Internal to the slice (not re-exported from index).
const PRIVACY_LEVELS = ['everybody', 'contacts', 'nobody'] as const;

export type PrivacyLevel = (typeof PRIVACY_LEVELS)[number];
// What a READ can report: Telegram may hold a rule the dashboard does not model.
export type PrivacyShown = PrivacyLevel | 'unknown';

// No radio-group primitive exists in the app, so the choice is the aria-pressed
// button idiom the settings page's provider switch already uses. An 'unknown'
// level presses nothing — it must never read as "everybody".
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
      className="flex items-center gap-3 rounded-[12px] border border-line px-[14px] py-3"
    >
      <div className="min-w-0 flex-1">
        <div className="truncate text-[13.5px] font-semibold">{label}</div>
        <div className="mt-[2px] text-[11.5px] text-ink-subtle">
          {t('accounts.profile.privacy.current', {
            value: t(`accounts.profile.privacy.level.${current}`),
          })}
        </div>
        {current === 'unknown' && (
          <div className="mt-[2px] text-[11.5px] text-ink-muted">
            {t('accounts.profile.privacy.unknownNote')}
          </div>
        )}
      </div>
      <div className="flex shrink-0 gap-1">
        {PRIVACY_LEVELS.map((level) => (
          <button
            key={level}
            type="button"
            disabled={busy}
            aria-pressed={current === level}
            onClick={() => {
              onPick(level);
            }}
            className={`rounded-[8px] border px-[10px] py-[5px] text-[12px] font-medium transition-colors disabled:opacity-60 ${
              current === level
                ? 'border-primary bg-[#f2f6ff] text-primary'
                : 'border-line-input bg-white text-ink-muted hover:border-[#c8c6c2] hover:bg-[#f7f6f4]'
            }`}
          >
            {t(`accounts.profile.privacy.level.${level}`)}
          </button>
        ))}
      </div>
    </div>
  );
}
