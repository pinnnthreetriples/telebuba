import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { HintBubble, SegmentedControl } from '@/shared/ui';

// Fleet-wide choice of WHICH message the fleet answers: the post itself, or a human's
// comment under it — plus how long the reply mode holds a post open waiting for that
// human. Controlled and write-free on purpose: it lives inside the listener-edit modal,
// whose "Отмена" has to be able to throw the choice away, so the owner keeps the draft
// and does the single PUT on "Сохранить".
const MODES = ['first', 'reply'] as const;

export type CommentMode = (typeof MODES)[number];

// The schema's own ge=1/le=120, restated because the generated client carries neither:
// a field that let 0 or 500 into the draft would only earn a 422 on save.
const WAIT_MIN = 1;
const WAIT_MAX = 120;

export function CommentModeFields({
  mode,
  waitMinutes,
  disabled,
  onModeChange,
  onWaitChange,
}: {
  mode: CommentMode;
  waitMinutes: number;
  disabled: boolean;
  onModeChange: (mode: CommentMode) => void;
  onWaitChange: (minutes: number) => void;
}) {
  const { t } = useTranslation();
  // Held while the operator types: a field driven straight off the committed draft would
  // fight every keystroke back to the last whole number.
  const [typed, setTyped] = useState<string | null>(null);

  const commitWait = () => {
    if (typed === null) return;
    const next = Number(typed);
    // Out of bounds, fractional or empty: commit nothing and let the current value speak
    // again, so the field never shows a number the engine could not be asked to wait.
    if (Number.isInteger(next) && next >= WAIT_MIN && next <= WAIT_MAX) onWaitChange(next);
    setTyped(null);
  };

  return (
    <div className="mt-xl">
      <div className="mb-sm type-label">{t('neurocomment.mode.label')}</div>
      <SegmentedControl
        variant="outline"
        value={mode}
        ariaLabel={t('neurocomment.mode.label')}
        // Nothing to pick until the stored settings land: the save needs them to
        // build the PUT body, so a draft made before that could not be honoured.
        disabled={disabled}
        options={MODES.map((option) => ({
          value: option,
          // The hint hangs off the segment itself rather than a "?" badge beside it —
          // the badge is focusable, and nesting it inside a <button> would be invalid
          // markup. The bubble is not: it anchors on the segment's own `group relative`.
          label: (
            <>
              {t(`neurocomment.mode.${option}.label`)}
              <HintBubble
                text={t(`neurocomment.mode.${option}.hint`)}
                example={t(`neurocomment.mode.${option}.example`)}
              />
            </>
          ),
          // `title` is the native fallback the styled bubble does not replace: it is
          // what a touch device and a screen reader get, since neither hovers.
          title: `${t(`neurocomment.mode.${option}.hint`)}\n${t(`neurocomment.mode.${option}.example`)}`,
          // The bubble now lives INSIDE the option, so without this the option's
          // accessible name would be its label with the whole hint and example read
          // out after it. The name is the label; the hint stays the description.
          ariaLabel: t(`neurocomment.mode.${option}.label`),
        }))}
        onChange={(option) => {
          onModeChange(option);
        }}
      />

      {/* Only in reply mode, because that is the only mode the wait exists in: shown beside
          "пишем первыми" it would be a number the operator turns to no effect. */}
      {mode === 'reply' ? (
        <label className="mt-lg block">
          <span className="mb-sm block type-label">{t('neurocomment.mode.waitLabel')}</span>
          <span className="flex items-center gap-sm">
            <input
              type="number"
              min={WAIT_MIN}
              max={WAIT_MAX}
              step={1}
              inputMode="numeric"
              value={typed ?? String(waitMinutes)}
              disabled={disabled}
              onChange={(event) => {
                setTyped(event.target.value);
              }}
              // Blur is the commit, so the draft takes the whole number instead of the "4"
              // the operator was still turning into "45"; Enter is the same commit for an
              // operator who never leaves the field.
              onBlur={commitWait}
              onKeyDown={(event) => {
                if (event.key === 'Enter') event.currentTarget.blur();
              }}
              aria-label={t('neurocomment.mode.waitLabel')}
              className="tb-time w-number rounded-md border border-line bg-surface-card px-md py-tight text-body font-medium text-content-primary disabled:opacity-60"
            />
            <span className="type-caption">{t('neurocomment.mode.waitHint')}</span>
          </span>
        </label>
      ) : null}
    </div>
  );
}
