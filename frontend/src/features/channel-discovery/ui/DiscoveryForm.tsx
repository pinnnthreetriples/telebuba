import { useMutation } from '@tanstack/react-query';
import { useId, useRef } from 'react';
import { useTranslation } from 'react-i18next';

import { expandDiscoveryKeywordsMutation } from '@/entities/campaign';
import { Button, HelpHint, Input } from '@/shared/ui';

import {
  boundsInverted,
  canSubmit,
  EMPTY_FORM,
  KEYWORD_MAX_LENGTH,
  KEYWORD_MIN_LENGTH,
  MAX_KEYWORDS,
  mergeKeywords,
  splitKeywords,
  type DiscoveryFormState,
} from '../model/discovery';

// The project has no shared input primitive; this literal is the established
// convention (duplicated in _styles.ts, ApiKeyField.tsx, SettingsPage.tsx).
const LABEL = 'mb-tight block type-label';
// A HelpHint must sit OUTSIDE the <label>, so the label text needs its own row and the
// control needs an id: a label click activates its control, and on a phone tapping the
// badge is the only way to open a hover tooltip — which silently joined the tooltip
// prose to the field's accessible name.
const LABEL_ROW = `${LABEL} flex items-center gap-sm`;
const HINT = 'mt-tight block type-caption';

// Same contract as DiscoveryResults' reasonKey: the server sends short locale-neutral
// codes, and an unmapped one falls back to the code itself so a value added later
// degrades to something readable rather than to an empty line.
const expandErrorKey = (code: string) => `neurocomment.modal.discovery.form.expandError.${code}`;

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
  const keywordsId = useId();

  const set = <K extends keyof DiscoveryFormState>(key: K, value: DiscoveryFormState[K]) => {
    onChange({ ...form, [key]: value });
  };

  const expand = useMutation(expandDiscoveryKeywordsMutation());
  const topic = form.keywords.trim();
  // Code points, like splitKeywords: the endpoint's bound is Python's len().
  const topicTooLong = [...topic].length > KEYWORD_MAX_LENGTH;

  // The model answers seconds later and the operator may well keep typing meanwhile.
  // An onSuccess closure carries the form as it was at click time, so merging into it
  // would undo those keystrokes — the one thing this button must never do.
  const latest = useRef(form);
  latest.current = form;

  // Widen the typed topic, then APPEND — the suggestion is a starting point the
  // operator edits, and every keyword it adds is a real Telegram read out of the run's
  // budget. That decision is theirs, so this never starts the search itself.
  const suggest = () => {
    // One caller, and nothing to clean up if the modal closes mid-flight: the answer
    // is merged into local form state, so dropping it is the right outcome rather
    // than a lost write.
    expand.mutate(
      { body: { topic } },
      {
        onSuccess: (result) => {
          const merged = mergeKeywords(latest.current.keywords, result.keywords ?? []);
          onChange({ ...latest.current, keywords: merged });
        },
      },
    );
  };

  return (
    <form
      className="flex flex-col gap-lg"
      onSubmit={(event) => {
        event.preventDefault();
        if (canSubmit(form) && !submitting) onSubmit();
      }}
    >
      {/* Not a wrapping <label>: the suggest button's own text would join the input's
          accessible name, the same trap the seed field's HelpHint documents below. */}
      <div>
        <label htmlFor={keywordsId} className={LABEL}>
          {t('neurocomment.modal.discovery.form.keywords')}
        </label>
        <div className="flex items-start gap-sm">
          <Input
            id={keywordsId}
            autoFocus
            value={form.keywords}
            onChange={(event) => {
              set('keywords', event.target.value);
            }}
            placeholder={t('neurocomment.modal.discovery.form.keywordsPlaceholder')}
          />
          {/* type="button" is load-bearing: the default inside a <form> is submit, and
              submitting here would spend the run's Telegram read budget on keywords the
              operator has not looked at yet. */}
          <button
            type="button"
            onClick={suggest}
            disabled={topic === '' || topicTooLong || expand.isPending}
            className="shrink-0 whitespace-nowrap rounded-lg border border-line bg-surface-card px-lg py-md text-body font-medium text-content-muted transition-colors hover:border-action-primary hover:text-action-primary disabled:opacity-50"
          >
            {expand.isPending
              ? t('neurocomment.modal.discovery.form.expanding')
              : t('neurocomment.modal.discovery.form.expand')}
          </button>
        </div>
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

        {/* Say why the button went dead rather than truncating a topic the operator
            wrote — a silent cut would ask the model about something else. */}
        {topicTooLong ? (
          <p role="status" className="mt-tight type-caption text-danger">
            {t('neurocomment.modal.discovery.form.expandTooLong', { max: KEYWORD_MAX_LENGTH })}
          </p>
        ) : null}

        {/* A 200 carrying a code: nothing was expanded, and each code points at a
            different fix. Unmapped codes fall back to the raw code. */}
        {expand.data?.error != null ? (
          <p role="status" className="mt-tight type-caption text-danger">
            {t(expandErrorKey(expand.data.error), { defaultValue: expand.data.error })}
          </p>
        ) : null}

        {/* The request itself never landed, so there is no code to translate — and the
            button silently re-enabling would read as "the model had nothing to say". */}
        {expand.isError ? (
          <p role="status" className="mt-tight type-caption text-danger">
            {t('neurocomment.modal.discovery.form.expandFailed')}
          </p>
        ) : null}
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-lg">
        <label className="block">
          <span className={LABEL}>{t('neurocomment.modal.discovery.form.minSubscribers')}</span>
          <Input
            type="number"
            min={0}
            value={form.minSubscribers}
            onChange={(event) => {
              set('minSubscribers', event.target.value);
            }}
            placeholder="0"
          />
        </label>
        <label className="block">
          <span className={LABEL}>{t('neurocomment.modal.discovery.form.maxSubscribers')}</span>
          <Input
            type="number"
            min={0}
            value={form.maxSubscribers}
            onChange={(event) => {
              set('maxSubscribers', event.target.value);
            }}
            placeholder="∞"
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
        <p className="type-caption text-danger">
          {t('neurocomment.modal.discovery.form.boundsInverted')}
        </p>
      ) : null}

      <div>
        <span className={LABEL_ROW}>
          <label htmlFor={seedId}>{t('neurocomment.modal.discovery.form.seedChannel')}</label>
          <HelpHint text={t('neurocomment.modal.discovery.form.seedChannelHint')} />
        </span>
        <Input
          id={seedId}
          value={form.seedChannel}
          onChange={(event) => {
            set('seedChannel', event.target.value);
          }}
          placeholder={t('neurocomment.modal.discovery.form.seedChannelPlaceholder')}
        />
      </div>

      <div className="flex items-center justify-end gap-md pt-xs">
        <button
          type="button"
          onClick={() => {
            onChange(EMPTY_FORM);
          }}
          className="rounded-lg px-lg py-sm text-body text-content-muted hover:text-action-primary"
        >
          {t('neurocomment.modal.discovery.form.reset')}
        </button>
        <Button type="submit" variant="primary" size="sm" disabled={!canSubmit(form) || submitting}>
          {submitting
            ? t('neurocomment.modal.discovery.form.searching')
            : t('neurocomment.modal.discovery.form.submit')}
        </Button>
      </div>
    </form>
  );
}
