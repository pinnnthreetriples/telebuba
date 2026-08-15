import { useId } from 'react';
import { useTranslation } from 'react-i18next';

import { HelpHint } from '@/shared/ui';

import {
  boundsInverted,
  canSubmit,
  EMPTY_FORM,
  KEYWORD_MIN_LENGTH,
  MAX_KEYWORDS,
  splitKeywords,
  type DiscoveryFormState,
} from '../model/discovery';

// The project has no shared input primitive; this literal is the established
// convention (duplicated in _styles.ts, ApiKeyField.tsx, SettingsPage.tsx).
const FIELD =
  'tb-time w-full rounded-[10px] border border-line-input bg-white px-3 py-[9px] text-[13px] outline-none';
const LABEL = 'mb-[6px] block text-[12px] font-medium text-[#3a3a3a]';
// A HelpHint must sit OUTSIDE the <label>, so the label text needs its own row and the
// control needs an id: a label click activates its control, and on a phone tapping the
// badge is the only way to open a hover tooltip — which silently joined the tooltip
// prose to the field's accessible name.
const LABEL_ROW = `${LABEL} flex items-center gap-[6px]`;
const HINT = 'mt-[5px] block text-[11.5px] text-ink-subtle';

type Props = {
  form: DiscoveryFormState;
  submitting: boolean;
  onChange: (form: DiscoveryFormState) => void;
  onSubmit: () => void;
};

export function DiscoveryForm({ form, submitting, onChange, onSubmit }: Props) {
  const { t } = useTranslation();
  const { keywords: parsed, dropped } = splitKeywords(form.keywords);
  const inverted = boundsInverted(form);
  const seedId = useId();

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
        <span className={HINT}>
          {t('neurocomment.modal.discovery.form.keywordsHint', {
            min: KEYWORD_MIN_LENGTH,
            max: MAX_KEYWORDS,
            parsed: parsed.length,
          })}
        </span>
        {/* Naming the tokens, not counting them: a silently dropped word (or a submit
            button disabled because every word was too short) explains nothing. */}
        {dropped.length > 0 ? (
          <span className={HINT}>
            {t('neurocomment.modal.discovery.form.keywordsDropped', {
              tokens: dropped.join(', '),
              min: KEYWORD_MIN_LENGTH,
              max: MAX_KEYWORDS,
            })}
          </span>
        ) : null}
      </label>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-[13px]">
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

      {/* What the bounds actually do. Telegram's own search returns a subscriber count
          for only some hits, and the rest enter the list unfiltered — with the number
          arriving later, from the comment check. Unsaid, a row that plainly breaks the
          filter looks like a bug in the filter. */}
      <span className={HINT}>{t('neurocomment.modal.discovery.form.membersHint')}</span>

      {/* The API refuses members_min > members_max, and canSubmit blocks it — without
          this the Search button would just go dead naming no field. */}
      {inverted ? (
        <p className="text-[11.5px] text-danger">
          {t('neurocomment.modal.discovery.form.boundsInverted')}
        </p>
      ) : null}

      <div>
        <span className={LABEL_ROW}>
          <label htmlFor={seedId}>{t('neurocomment.modal.discovery.form.seedChannel')}</label>
          <HelpHint text={t('neurocomment.modal.discovery.form.seedChannelHint')} />
        </span>
        <input
          id={seedId}
          value={form.seedChannel}
          onChange={(event) => {
            set('seedChannel', event.target.value);
          }}
          placeholder={t('neurocomment.modal.discovery.form.seedChannelPlaceholder')}
          className={FIELD}
        />
      </div>

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
