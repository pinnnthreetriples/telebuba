import { useMutation } from '@tanstack/react-query';
import { useId, useRef } from 'react';
import { useTranslation } from 'react-i18next';

import { expandDiscoveryKeywordsMutation } from '@/entities/campaign';
import { Button, Input } from '@/shared/ui';

import {
  KEYWORD_MAX_LENGTH,
  KEYWORD_MIN_LENGTH,
  MAX_KEYWORDS,
  mergeKeywords,
  splitKeywords,
  type DiscoveryFormState,
} from '../model/discovery';
import { Eyebrow } from './FormRow';

const P = 'neurocomment.modal.discovery.form';

// Same contract as DiscoveryResults' reasonKey: the server sends short locale-neutral
// codes, and an unmapped one falls back to the code itself so a value added later
// degrades to something readable rather than to an empty line.
const expandErrorKey = (code: string) => `${P}.expandError.${code}`;

type Props = {
  form: DiscoveryFormState;
  onChange: (form: DiscoveryFormState) => void;
};

// Блок «Запрос»: поле ключевых слов и подбор слов моделью. / The query block.
export function KeywordsField({ form, onChange }: Props) {
  const { t } = useTranslation();
  const id = useId();
  const { keywords: parsed, dropped } = splitKeywords(form.keywords);

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
    <section>
      <Eyebrow
        title={t(`${P}.sections.query`)}
        caption={t(`${P}.keywordsHint`, {
          min: KEYWORD_MIN_LENGTH,
          max: MAX_KEYWORDS,
          parsed: parsed.length,
        })}
      />
      {/* Not a wrapping <label>: the suggest button's own text would join the input's
          accessible name. */}
      <label htmlFor={id} className="mb-tight block type-label">
        {t(`${P}.keywords`)}
      </label>
      <div className="flex items-start gap-sm">
        <Input
          id={id}
          size="md"
          autoFocus
          value={form.keywords}
          onChange={(event) => {
            onChange({ ...form, keywords: event.target.value });
          }}
          placeholder={t(`${P}.keywordsPlaceholder`)}
        />
        {/* type="button" is load-bearing: the default inside a <form> is submit, and
            submitting here would spend the run's Telegram read budget on keywords the
            operator has not looked at yet. */}
        <Button
          type="button"
          size="md"
          variant="secondary"
          className="shrink-0 whitespace-nowrap"
          loading={expand.isPending}
          disabled={topic === '' || topicTooLong}
          onClick={suggest}
        >
          {expand.isPending ? t(`${P}.expanding`) : t(`${P}.expand`)}
        </Button>
      </div>
      {/* Naming the tokens, not counting them: a silently dropped word (or a submit
          button disabled because every word was too short) explains nothing. */}
      {dropped.length > 0 ? (
        <span className="mt-tight block type-caption">
          {t(`${P}.keywordsDropped`, {
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
          {t(`${P}.expandTooLong`, { max: KEYWORD_MAX_LENGTH })}
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
          {t(`${P}.expandFailed`)}
        </p>
      ) : null}
    </section>
  );
}
