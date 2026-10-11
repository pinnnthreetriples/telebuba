import { useMutation } from '@tanstack/react-query';
import { useId, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { spamCheckAccountMutation } from '@/entities/account';
import { HEADING_ICON_TILE } from '@/shared/design-system';
import { Button, Icon, Modal, SegmentedControl } from '@/shared/ui';

const MIN = 1;
// The slider's usual end; a longer configured default widens it (see `max` below).
const MAX = 30;
const PRESETS = [7, 14, 30];
// A dot per preset (plus the floor), not per day: thirty dots on one track read as noise.
const TICKS = [MIN, ...PRESETS];
// Only until the board answers; the board's `default_target_days` is the real default.
const FALLBACK_DAYS = 14;

type ActivityPersona = 'calm' | 'normal' | 'active';
const PERSONAS: ActivityPersona[] = ['calm', 'normal', 'active'];

type SpamState = 'idle' | 'loading' | 'clean' | 'limited';

const DEFAULT_PERSONA: ActivityPersona = 'normal';

// Arrow/Home/End steps for the day slider, given its current upper end.
const SLIDER_KEYS: Record<string, (days: number, max: number) => number> = {
  ArrowLeft: (d) => Math.max(MIN, d - 1),
  ArrowRight: (d, max) => Math.min(max, d + 1),
  Home: () => MIN,
  End: (_d, max) => max,
};

// The design's "warm account" modal: a draggable day-length slider (1–30) that opens
// on the length a start would run anyway (the board's `default_target_days`), quick
// presets, an activity persona (cadence), and a real @SpamBot pre-check.
export function WarmDaysModal({
  accountId,
  phone,
  defaultDays = FALLBACK_DAYS,
  onClose,
  onConfirm,
}: {
  accountId: string;
  phone: string;
  defaultDays?: number;
  onClose: () => void;
  onConfirm: (days: number, persona: ActivityPersona) => void;
}) {
  const { t } = useTranslation();
  // The configured default is never cut down: a 45-day `warmed_min_days` would
  // otherwise start a 30-day stint just because the operator pressed Start.
  const initialDays = Math.max(MIN, Math.round(defaultDays));
  const max = Math.max(MAX, initialDays);
  const dayRatio = (n: number) => ((n - MIN) / (max - MIN)) * 100;
  const [days, setDays] = useState(initialDays);
  const [persona, setPersona] = useState<ActivityPersona>(DEFAULT_PERSONA);
  // A length or cadence picked and not yet started.
  const dirty = days !== initialDays || persona !== DEFAULT_PERSONA;
  const [spam, setSpam] = useState<SpamState>('idle');
  const spamMutation = useMutation(spamCheckAccountMutation());
  const trackRef = useRef<HTMLDivElement>(null);
  const spamTipId = useId();
  const personaTipId = useId();
  const pct = dayRatio(days);
  const dayCount = (n: number) => `${String(n)} ${t('warming.days.label', { count: n })}`;

  // Real @SpamBot probe against this account; the result is shown on the pill.
  const runSpamCheck = () => {
    setSpam('loading');
    spamMutation.mutate(
      { path: { account_id: accountId } },
      {
        onSuccess: (verdict) => {
          setSpam(verdict.status === 'clean' ? 'clean' : 'limited');
        },
        onError: () => {
          setSpam('limited');
        },
      },
    );
  };

  const setFromClientX = (clientX: number) => {
    const el = trackRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const ratio = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
    setDays(Math.round(MIN + ratio * (max - MIN)));
  };

  return (
    <Modal onClose={onClose} dirty={dirty} size="confirm" label={t('warming.days.title')}>
      {(close) => (
        <div className="p-6">
          <div className="mb-1 flex items-start gap-3">
            <div className={HEADING_ICON_TILE}>
              <svg
                width="17"
                height="17"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.9"
              >
                <circle cx="12" cy="12" r="3.4" />
                <path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M19.1 4.9 17 7M7 17l-2.1 2.1" />
              </svg>
            </div>
            <div className="flex-1 type-h2">{t('warming.days.title')}</div>
            <span className="tb-tip inline-flex shrink-0">
              {/* Already a tab stop, so `:focus-within` reveals the bubble for free; the
                `aria-describedby` is what names it. See app/styles/index.css. */}
              <button
                type="button"
                aria-describedby={spamTipId}
                disabled={spam === 'loading'}
                onClick={runSpamCheck}
                className={`inline-flex items-center gap-2 rounded-full border bg-surface-card px-3 py-1 text-body font-medium disabled:opacity-60 ${
                  spam === 'clean'
                    ? 'border-success text-success-deep'
                    : spam === 'limited'
                      ? 'border-danger text-danger'
                      : 'border-line text-content-muted'
                }`}
              >
                <Icon name="shield-check" size={14} />
                {spam === 'loading'
                  ? t('warming.days.spamChecking')
                  : spam === 'clean'
                    ? t('warming.days.spamClean')
                    : spam === 'limited'
                      ? t('warming.days.spamLimited')
                      : t('warming.days.spamCheck')}
              </button>
              <span id={spamTipId} role="tooltip" className="tb-tip-pop">
                {t('warming.days.spamTip')}
              </span>
            </span>
          </div>
          <div className="mb-6 type-body text-content-muted">
            {t('warming.days.subtitle', { phone })}
          </div>

          <div className="mb-6 text-center">
            <div className="text-h1 font-medium leading-none text-action-primary">{days}</div>
            <div className="mt-1 type-body text-content-muted">
              {t('warming.days.label', { count: days })}
            </div>
          </div>

          <div
            ref={trackRef}
            role="slider"
            tabIndex={0}
            aria-valuemin={MIN}
            aria-valuemax={max}
            aria-valuenow={days}
            aria-valuetext={dayCount(days)}
            aria-label={t('warming.days.title')}
            onPointerDown={(e) => {
              e.currentTarget.setPointerCapture(e.pointerId);
              setFromClientX(e.clientX);
            }}
            onPointerMove={(e) => {
              if (e.buttons === 1) setFromClientX(e.clientX);
            }}
            onKeyDown={(e) => {
              const step = SLIDER_KEYS[e.key];
              if (!step) return;
              // Home/End would otherwise also scroll the dialog under the slider.
              e.preventDefault();
              setDays((d) => step(d, max));
            }}
            className="relative mx-3 mb-2 h-compact cursor-grab touch-none select-none outline-hidden"
          >
            <div className="absolute inset-x-0 top-1/2 h-meter -translate-y-1/2 overflow-hidden rounded-full bg-canvas">
              <div
                className="h-full rounded-full bg-action-primary"
                style={{ width: `${String(pct)}%` }}
              />
            </div>
            <div className="pointer-events-none absolute inset-x-0 top-1/2 h-meter -translate-y-1/2">
              {TICKS.map((n) => (
                <span
                  key={n}
                  className="absolute top-1/2 size-tick -translate-x-1/2 -translate-y-1/2 rounded-full bg-line-strong"
                  style={{ left: `${String(dayRatio(n))}%` }}
                />
              ))}
            </div>
            <div
              className="absolute top-1/2 size-glyph -translate-x-1/2 -translate-y-1/2 rounded-full border-[2px] border-action-primary bg-surface-card shadow-thumb"
              style={{ left: `${String(pct)}%` }}
            />
          </div>
          <div className="mx-3 mb-6 flex justify-between type-small">
            <span>{dayCount(MIN)}</span>
            <span>{dayCount(max)}</span>
          </div>

          <SegmentedControl
            variant="outline"
            className="mb-6"
            // The presets are numbers and the control keys on strings, so the value it
            // carries is the number's own text; the handler puts the number back.
            value={String(days)}
            options={PRESETS.map((n) => ({
              value: String(n),
              label: dayCount(n),
            }))}
            onChange={(value) => {
              setDays(Number(value));
            }}
          />

          <div className="mb-2 flex items-center gap-2 type-body-medium">
            {t('warming.persona.label')}
            <span className="tb-tip inline-flex">
              <button
                type="button"
                aria-label={t('warming.persona.label')}
                aria-describedby={personaTipId}
                className="inline-flex size-glyph items-center justify-center rounded-full border border-line text-small font-medium text-content-subtle"
              >
                ?
              </button>
              <span id={personaTipId} role="tooltip" className="tb-tip-pop">
                {t('warming.persona.tip')}
              </span>
            </span>
          </div>
          <SegmentedControl
            variant="outline"
            className="mb-6"
            value={persona}
            ariaLabel={t('warming.persona.label')}
            options={PERSONAS.map((p) => ({
              value: p,
              label: (
                <>
                  <div className="type-body-medium">{t(`warming.persona.${p}.name`)}</div>
                  <div className="mt-1 type-small">{t(`warming.persona.${p}.hint`)}</div>
                </>
              ),
            }))}
            onChange={(p) => {
              setPersona(p);
            }}
          />

          <div className="flex justify-end gap-2">
            <Button
              variant="primary"
              onClick={() => {
                onConfirm(days, persona);
                onClose();
              }}
            >
              {t('warming.days.start')}
            </Button>
            <Button onClick={close}>{t('warming.days.cancel')}</Button>
          </div>
        </div>
      )}
    </Modal>
  );
}
