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
//
// Deliberately NOT role="radio"/radiogroup: a radiogroup promises arrow-key
// navigation with a single tab stop, which we do not implement — a keyboard
// user would land in a group whose options cannot be reached. Three toggles
// that each take focus are honest about how they actually behave.
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
        <div className="truncate text-lead font-semibold">{label}</div>
        <div className="mt-hair text-tiny text-ink-subtle">
          {t('accounts.profile.privacy.current', {
            value: t(`accounts.profile.privacy.level.${current}`),
          })}
        </div>
        {current === 'unknown' && (
          <div className="mt-hair text-tiny text-ink-muted">
            {t('accounts.profile.privacy.unknownNote')}
          </div>
        )}
      </div>
      <div className="flex shrink-0 gap-tight">
        {PRIVACY_LEVELS.map((level) => (
          <button
            key={level}
            type="button"
            disabled={busy}
            // The visible text is only «Все»/«Контакты»/«Никто», so all three
            // rows together expose nine buttons with identical names in an
            // element list. The row goes into the accessible name.
            aria-label={`${label}: ${t(`accounts.profile.privacy.level.${level}`)}`}
            aria-pressed={current === level}
            onClick={() => {
              onPick(level);
            }}
            className={`rounded-md border px-md py-tight text-body font-medium transition-colors disabled:opacity-60 ${
              current === level
                ? 'border-primary bg-primary-tint text-primary-deep'
                : 'border-line bg-white text-ink-muted hover:border-line-strong hover:bg-surface'
            }`}
          >
            {t(`accounts.profile.privacy.level.${level}`)}
          </button>
        ))}
      </div>
    </div>
  );
}
