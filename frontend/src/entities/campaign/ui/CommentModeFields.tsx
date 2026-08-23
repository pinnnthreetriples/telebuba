import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { HintBubble } from '@/shared/ui';

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
    <div className="mt-[18px]">
      <div role="group" aria-label={t('neurocomment.mode.label')}>
        <div className="mb-[7px] text-body font-medium text-ink-body">
          {t('neurocomment.mode.label')}
        </div>
        <div className="flex gap-tight">
          {MODES.map((option) => (
            // `group relative` is what anchors the bubble; the wrapper carries the `flex-1`
            // the button used to, so the two options still split the row evenly. The hint
            // hangs off the BUTTON rather than a "?" badge beside it — the badge is
            // focusable, and nesting it inside a <button> would be invalid markup.
            <span key={option} className="group relative flex-1">
              <button
                type="button"
                // The aria-pressed idiom PrivacyLevelRow documents: two honest toggles, not a
                // radiogroup whose arrow-key navigation the app does not implement.
                aria-pressed={mode === option}
                // Nothing to pick until the stored settings land: the save needs them to
                // build the PUT body, so a draft made before that could not be honoured.
                disabled={disabled}
                onClick={() => {
                  onModeChange(option);
                }}
                // `title` is the native fallback the styled bubble does not replace: it is
                // what a touch device and a screen reader get, since neither hovers.
                title={`${t(`neurocomment.mode.${option}.hint`)}\n${t(`neurocomment.mode.${option}.example`)}`}
                className={`w-full rounded-md border px-[10px] py-[6px] text-body font-medium transition-colors disabled:opacity-60 ${
                  mode === option
                    ? 'border-primary bg-primary-wash text-primary'
                    : 'border-line-input bg-white text-ink-muted hover:border-line-strong hover:bg-surface'
                }`}
              >
                {t(`neurocomment.mode.${option}.label`)}
              </button>
              <HintBubble
                text={t(`neurocomment.mode.${option}.hint`)}
                example={t(`neurocomment.mode.${option}.example`)}
              />
            </span>
          ))}
        </div>
      </div>

      {/* Only in reply mode, because that is the only mode the wait exists in: shown beside
          "пишем первыми" it would be a number the operator turns to no effect. */}
      {mode === 'reply' ? (
        <label className="mt-[13px] block">
          <span className="mb-[7px] block text-body font-medium text-ink-body">
            {t('neurocomment.mode.waitLabel')}
          </span>
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
              className="tb-time w-[68px] rounded-md border border-line-input bg-white px-[10px] py-[6px] text-body font-medium text-ink disabled:opacity-60"
            />
            <span className="text-tiny text-ink-subtle">{t('neurocomment.mode.waitHint')}</span>
          </span>
        </label>
      ) : null}
    </div>
  );
}
