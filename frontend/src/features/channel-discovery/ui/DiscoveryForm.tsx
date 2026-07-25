import { useTranslation } from 'react-i18next';

import { HelpHint } from '@/shared/ui';

import {
  canSubmit,
  EMPTY_FORM,
  KEYWORD_MIN_LENGTH,
  MAX_KEYWORDS,
  parseKeywords,
  type DiscoveryFormState,
} from '../model/discovery';

// The project has no shared input primitive; this literal is the established
// convention (duplicated in _styles.ts, ApiKeyField.tsx, SettingsPage.tsx).
const FIELD =
  'tb-time w-full rounded-[10px] border border-line-input bg-white px-3 py-[9px] text-[13px] outline-none';
const LABEL = 'mb-[6px] block text-[12px] font-medium text-[#3a3a3a]';
const CHECKBOX = 'h-[14px] w-[14px] shrink-0 accent-primary disabled:opacity-40';

// Curated against Telemetr.io's dictionaries: the regions this fleet targets
// (CIS, Europe, MENA). Labels come from Intl.DisplayNames, so ru/en are free.
const LANGUAGES = ['ru', 'en', 'ar', 'de', 'fr', 'es', 'tr', 'uk', 'kk', 'uz', 'fa', 'hi'];
const COUNTRIES = ['RU', 'KZ', 'UZ', 'UA', 'BY', 'DE', 'FR', 'ES', 'GB', 'TR', 'AE', 'SA', 'EG'];

type Props = {
  form: DiscoveryFormState;
  telemetrConfigured: boolean;
  submitting: boolean;
  onChange: (form: DiscoveryFormState) => void;
  onSubmit: () => void;
};

export function DiscoveryForm({ form, telemetrConfigured, submitting, onChange, onSubmit }: Props) {
  const { t, i18n } = useTranslation();
  // Intl.DisplayNames throws on an empty locale, which is what i18n.language is
  // before init resolves; fall back to the app default rather than crashing.
  const locale = i18n.language || 'ru';
  const languageNames = new Intl.DisplayNames([locale], { type: 'language' });
  const regionNames = new Intl.DisplayNames([locale], { type: 'region' });
  const parsed = parseKeywords(form.keywords);

  const set = <K extends keyof DiscoveryFormState>(key: K, value: DiscoveryFormState[K]) => {
    onChange({ ...form, [key]: value });
  };

  return (
    <form
      className="flex flex-col gap-[13px]"
      onSubmit={(event) => {
        event.preventDefault();
        if (canSubmit(form) && !submitting) onSubmit();
      }}
    >
      <label className="block">
        <span className={LABEL}>{t('neurocomment.modal.discovery.form.keywords')}</span>
        <input
          autoFocus
          value={form.keywords}
          onChange={(event) => {
            set('keywords', event.target.value);
          }}
          placeholder={t('neurocomment.modal.discovery.form.keywordsPlaceholder')}
          className={FIELD}
        />
        <span className="mt-[5px] block text-[11.5px] text-ink-subtle">
          {t('neurocomment.modal.discovery.form.keywordsHint', {
            min: KEYWORD_MIN_LENGTH,
            max: MAX_KEYWORDS,
            count: parsed.length,
          })}
        </span>
      </label>

      <div className="grid grid-cols-2 gap-[13px]">
        <label className="block">
          <span className={LABEL}>{t('neurocomment.modal.discovery.form.language')}</span>
          <select
            value={form.language}
            onChange={(event) => {
              set('language', event.target.value);
            }}
            className={FIELD}
          >
            <option value="">{t('neurocomment.modal.discovery.form.anyLanguage')}</option>
            {LANGUAGES.map((code) => (
              <option key={code} value={code}>
                {languageNames.of(code) ?? code}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className={LABEL}>{t('neurocomment.modal.discovery.form.country')}</span>
          <select
            value={form.country}
            onChange={(event) => {
              set('country', event.target.value);
            }}
            className={FIELD}
          >
            <option value="">{t('neurocomment.modal.discovery.form.anyCountry')}</option>
            {COUNTRIES.map((code) => (
              <option key={code} value={code}>
                {regionNames.of(code) ?? code}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className={LABEL}>{t('neurocomment.modal.discovery.form.minSubscribers')}</span>
          <input
            type="number"
            min={0}
            value={form.minSubscribers}
            onChange={(event) => {
              set('minSubscribers', event.target.value);
            }}
            placeholder="0"
            className={FIELD}
          />
        </label>
        <label className="block">
          <span className={LABEL}>{t('neurocomment.modal.discovery.form.maxSubscribers')}</span>
          <input
            type="number"
            min={0}
            value={form.maxSubscribers}
            onChange={(event) => {
              set('maxSubscribers', event.target.value);
            }}
            placeholder="∞"
            className={FIELD}
          />
        </label>
      </div>

      <label className="block">
        <span className={LABEL}>
          {t('neurocomment.modal.discovery.form.seedChannel')}
          <HelpHint text={t('neurocomment.modal.discovery.form.seedChannelHint')} />
        </span>
        <input
          value={form.seedChannel}
          onChange={(event) => {
            set('seedChannel', event.target.value);
          }}
          placeholder={t('neurocomment.channels.placeholder')}
          className={FIELD}
        />
      </label>

      <label className="flex items-center gap-[8px]">
        <input
          type="checkbox"
          checked={form.useTelemetr}
          disabled={!telemetrConfigured}
          onChange={(event) => {
            set('useTelemetr', event.target.checked);
          }}
          aria-label={t('neurocomment.modal.discovery.form.useTelemetr')}
          className={CHECKBOX}
        />
        <span className="text-[12.5px] text-ink-muted">
          {t('neurocomment.modal.discovery.form.useTelemetr')}
          <HelpHint
            text={t(
              telemetrConfigured
                ? 'neurocomment.modal.discovery.form.useTelemetrHint'
                : 'neurocomment.modal.discovery.form.useTelemetrMissing',
            )}
          />
        </span>
      </label>

      <div className="flex items-center justify-end gap-[9px] pt-[3px]">
        <button
          type="button"
          onClick={() => {
            onChange(EMPTY_FORM);
          }}
          className="rounded-[10px] px-[13px] py-[8px] text-[12.5px] text-ink-muted hover:text-primary"
        >
          {t('neurocomment.modal.discovery.form.reset')}
        </button>
        <button
          type="submit"
          disabled={!canSubmit(form) || submitting}
          className="rounded-[10px] bg-primary px-[15px] py-[8px] text-[12.5px] font-medium text-white disabled:opacity-50"
        >
          {submitting
            ? t('neurocomment.modal.discovery.form.searching')
            : t('neurocomment.modal.discovery.form.submit')}
        </button>
      </div>
    </form>
  );
}
