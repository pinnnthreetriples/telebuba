import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  neurocommentSettingsQueryOptions,
  updateNeurocommentSettingsMutation,
} from '@/entities/campaign';
import type { NeurocommentSettingsUpdate } from '@/shared/api';
import { toastError } from '@/shared/ui';

// Fleet-wide choice of WHICH message the fleet answers: the post itself, or a human's
// comment under it — plus how long the reply mode holds a post open waiting for that
// human. Its own component rather than more props on ListenerCard, which is already the
// page's biggest card — and the only setting on the neurocomment page, so it owns its
// read/write instead of threading two more props through the page.
const MODES = ['first', 'reply'] as const;

type CommentMode = (typeof MODES)[number];

// The schema's own ge=1/le=120 and the backend's fallback, restated because the generated
// client carries neither: a field that let 0 or 500 through would only earn a 422.
const WAIT_MIN = 1;
const WAIT_MAX = 120;
const WAIT_DEFAULT = 10;

export function CommentModeToggle() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const settings = useQuery(neurocommentSettingsQueryOptions());
  const save = useMutation(updateNeurocommentSettingsMutation());
  const current = settings.data;
  // Until the read lands, show the default the backend also falls back to, so the
  // control never renders with nothing pressed (which would read as a third state).
  const mode: CommentMode = current?.comment_mode ?? 'first';
  const wait = current?.reply_wait_minutes ?? WAIT_DEFAULT;
  // Held while the operator types: a field driven straight off the query would fight every
  // keystroke back to the stored number.
  const [draft, setDraft] = useState<string | null>(null);
  const busy = save.isPending || current === undefined;

  const write = (patch: Partial<NeurocommentSettingsUpdate>, failed: string) => {
    if (current === undefined) return;
    save.mutate(
      {
        // PUT /settings replaces the limits wholesale and forbids extra keys, so every
        // write from here carries the stored numbers back unchanged — and `updated_at`
        // cannot ride along. Omitted mode/wait mean "leave as stored".
        body: {
          max_comments_per_hour: current.max_comments_per_hour,
          max_comments_per_channel_per_day: current.max_comments_per_channel_per_day,
          reply_delay_min_seconds: current.reply_delay_min_seconds,
          reply_delay_max_seconds: current.reply_delay_max_seconds,
          min_trust_score: current.min_trust_score,
          ...patch,
        },
      },
      {
        onError: () => {
          toastError(t(failed));
        },
        onSettled: async () => {
          await queryClient.invalidateQueries({
            queryKey: neurocommentSettingsQueryOptions().queryKey,
          });
          // Only now, with the stored value back in hand: dropping the draft any earlier
          // would flash the old number, and dropping it never would leave a rejected
          // write's number on screen as if it had been saved.
          setDraft(null);
        },
      },
    );
  };

  const pick = (next: CommentMode) => {
    if (next === mode) return;
    write({ comment_mode: next }, 'neurocomment.mode.failed');
  };

  const commitWait = () => {
    if (draft === null) return;
    const next = Number(draft);
    // Out of bounds, fractional or empty: send nothing and let the stored value speak
    // again, so the field never shows a number the engine is not actually waiting.
    if (!Number.isInteger(next) || next < WAIT_MIN || next > WAIT_MAX || next === wait) {
      setDraft(null);
      return;
    }
    write({ reply_wait_minutes: next }, 'neurocomment.mode.waitFailed');
  };

  return (
    <div className="mt-[9px]">
      <div role="group" aria-label={t('neurocomment.mode.label')}>
        <div className="mb-[5px] text-[11px] font-semibold uppercase tracking-[.03em] text-ink-subtle">
          {t('neurocomment.mode.label')}
        </div>
        <div className="flex gap-1">
          {MODES.map((option) => (
            <button
              key={option}
              type="button"
              // The aria-pressed idiom PrivacyLevelRow documents: two honest toggles, not a
              // radiogroup whose arrow-key navigation the app does not implement.
              aria-pressed={mode === option}
              // `disabled` is the whole in-flight guard: a second click while the PUT is
              // open would send the same body again, because `mode` still reads the old
              // value until the invalidated query comes back.
              disabled={busy}
              onClick={() => {
                pick(option);
              }}
              className={`flex-1 rounded-[8px] border px-[10px] py-[6px] text-[12px] font-medium transition-colors disabled:opacity-60 ${
                mode === option
                  ? 'border-primary bg-[#f2f6ff] text-primary'
                  : 'border-line-input bg-white text-ink-muted hover:border-[#c8c6c2] hover:bg-[#f7f6f4]'
              }`}
            >
              {t(`neurocomment.mode.${option}`)}
            </button>
          ))}
        </div>
      </div>

      {/* Only in reply mode, because that is the only mode the wait exists in: shown beside
          "пишем первыми" it would be a number the operator turns to no effect. */}
      {mode === 'reply' ? (
        <label className="mt-[9px] block">
          <span className="mb-[5px] block text-[11px] font-semibold uppercase tracking-[.03em] text-ink-subtle">
            {t('neurocomment.mode.waitLabel')}
          </span>
          <span className="flex items-center gap-2">
            <input
              type="number"
              min={WAIT_MIN}
              max={WAIT_MAX}
              step={1}
              inputMode="numeric"
              value={draft ?? String(wait)}
              disabled={busy}
              onChange={(event) => {
                setDraft(event.target.value);
              }}
              // Blur is the commit, so the PUT waits for the whole number instead of
              // firing on "4" while the operator is still typing "45"; Enter is the same
              // commit for an operator who never leaves the field.
              onBlur={commitWait}
              onKeyDown={(event) => {
                if (event.key === 'Enter') event.currentTarget.blur();
              }}
              aria-label={t('neurocomment.mode.waitLabel')}
              className="tb-time w-[68px] rounded-[8px] border border-line-input bg-white px-[10px] py-[6px] text-[12px] font-medium text-ink disabled:opacity-60"
            />
            <span className="text-[11.5px] text-ink-subtle">{t('neurocomment.mode.waitHint')}</span>
          </span>
        </label>
      ) : null}
    </div>
  );
}
