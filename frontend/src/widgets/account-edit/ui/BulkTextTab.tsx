import { useTranslation } from 'react-i18next';

import { Icon, Input, Textarea } from '@/shared/ui';

import { TEXT_FIELDS, TEXT_MAX, type TextFieldKey } from './_profileShared';

// Текст: the profile form's three writable fields, each behind its own checkbox.
//
// An unticked field is OMITTED from the request rather than sent empty — the
// backend's field contract ("" clears, absent leaves unchanged) is what makes
// "set one bio for the fleet, touch nothing else" expressible. A ticked-but-empty
// last name or bio therefore CLEARS it, which is the only way to wipe a field
// across a batch; the first name has no such state (Telegram has no nameless
// user), so an empty one blocks the apply instead.
//
// The username is absent by design, not forgotten: Telegram handles are unique,
// so one value cannot be given to a group at all.
export function BulkTextTab({
  on,
  value,
  onToggle,
  onValue,
}: {
  on: Record<TextFieldKey, boolean>;
  value: Record<TextFieldKey, string>;
  onToggle: (key: TextFieldKey) => void;
  onValue: (key: TextFieldKey, value: string) => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex flex-col gap-lg">
      <div className="rounded-lg bg-info-tint px-md py-md type-prose">
        {t('accounts.bulk.hint')}
      </div>
      {TEXT_FIELDS.map((key) => {
        const label = t(`accounts.bulk.field.${key}`);
        const empty = value[key].trim() === '';
        return (
          <div key={key} className="flex flex-col gap-tight">
            <button
              type="button"
              role="checkbox"
              aria-checked={on[key]}
              onClick={() => {
                onToggle(key);
              }}
              className="flex items-center gap-md text-left"
            >
              <span
                className={`flex size-glyph shrink-0 items-center justify-center rounded-sm border ${on[key] ? 'border-action-primary bg-action-primary' : 'border-line bg-surface-card'}`}
              >
                {on[key] && <Icon name="check" size={14} className="stroke-on-action" />}
              </span>
              <span className="type-label">{label}</span>
            </button>
            {key === 'bio' ? (
              <Textarea
                className="resize-none [font-family:inherit]"
                rows={3}
                disabled={!on[key]}
                value={value[key]}
                aria-label={label}
                onChange={(event) => {
                  onValue(key, event.target.value);
                }}
              />
            ) : (
              <Input
                disabled={!on[key]}
                value={value[key]}
                aria-label={label}
                onChange={(event) => {
                  onValue(key, event.target.value);
                }}
              />
            )}
            {on[key] && value[key].trim().length > TEXT_MAX[key] && (
              <span role="alert" className="type-caption font-medium text-danger">
                {t('accounts.bulk.tooLong', { max: TEXT_MAX[key] })}
              </span>
            )}
            {on[key] && empty && key === 'first_name' && (
              <span role="alert" className="type-caption font-medium text-danger">
                {t('accounts.profile.errFirstName')}
              </span>
            )}
            {on[key] && empty && key !== 'first_name' && (
              <span className="type-caption">{t('accounts.bulk.clears')}</span>
            )}
          </div>
        );
      })}
      <div className="type-caption">{t('accounts.bulk.usernameNote')}</div>
    </div>
  );
}
