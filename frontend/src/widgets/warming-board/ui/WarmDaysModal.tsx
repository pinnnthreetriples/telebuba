import { useMutation } from '@tanstack/react-query';
import { useId, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { spamCheckAccountMutation } from '@/entities/account';
import { Button, Icon, Modal, SegmentedControl } from '@/shared/ui';

const MIN = 1;
const MAX = 14;
const PRESETS = [3, 7, 14];
const TICKS = [...Array(MAX).keys()];

type ActivityPersona = 'calm' | 'normal' | 'active';
const PERSONAS: ActivityPersona[] = ['calm', 'normal', 'active'];

type SpamState = 'idle' | 'loading' | 'clean' | 'limited';

// The design's "warm account" modal: a draggable day-length slider (1–14),
// quick presets, an activity persona (cadence), and a real @SpamBot pre-check.
export function WarmDaysModal({
  accountId,
  phone,
  onClose,
  onConfirm,
}: {
  accountId: string;
  phone: string;
  onClose: () => void;
  onConfirm: (days: number, persona: ActivityPersona) => void;
}) {
  const { t } = useTranslation();
  const [days, setDays] = useState(7);
  const [persona, setPersona] = useState<ActivityPersona>('normal');
  const [spam, setSpam] = useState<SpamState>('idle');
  const spamMutation = useMutation(spamCheckAccountMutation());
  const trackRef = useRef<HTMLDivElement>(null);
  const spamTipId = useId();
  const personaTipId = useId();
  const pct = ((days - MIN) / (MAX - MIN)) * 100;

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
    setDays(Math.round(MIN + ratio * (MAX - MIN)));
  };

  return (
    <Modal onClose={onClose} className="w-confirm" label={t('warming.days.title')}>
      <div className="p-2xl">
        <div className="mb-xs flex items-start gap-md">
          <div className="flex size-tile shrink-0 items-center justify-center rounded-lg bg-primary-tint text-primary-deep">
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
          <div className="flex-1 type-dialog-title">{t('warming.days.title')}</div>
          <span className="tb-tip inline-flex shrink-0">
            {/* Already a tab stop, so `:focus-within` reveals the bubble for free; the
                `aria-describedby` is what names it. See app/styles/index.css. */}
            <button
              type="button"
              aria-describedby={spamTipId}
              disabled={spam === 'loading'}
              onClick={runSpamCheck}
              className={`inline-flex items-center gap-sm rounded-full border bg-white px-md py-tight text-body font-medium disabled:opacity-60 ${
                spam === 'clean'
                  ? 'border-success text-success-deep'
                  : spam === 'limited'
                    ? 'border-danger text-danger'
                    : 'border-line text-ink-muted'
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
        <div className="mb-2xl type-dialog-body">{t('warming.days.subtitle', { phone })}</div>

        <div className="mb-xl text-center">
          <div className="text-hero font-bold leading-none tracking-[-0.02em] text-primary">
            {days}
          </div>
          <div className="mt-xs type-dialog-body">{t('warming.days.label', { count: days })}</div>
        </div>

        <div
          ref={trackRef}
          role="slider"
          tabIndex={0}
          aria-valuemin={MIN}
          aria-valuemax={MAX}
          aria-valuenow={days}
          onPointerDown={(e) => {
            e.currentTarget.setPointerCapture(e.pointerId);
            setFromClientX(e.clientX);
          }}
          onPointerMove={(e) => {
            if (e.buttons === 1) setFromClientX(e.clientX);
          }}
          onKeyDown={(e) => {
            if (e.key === 'ArrowLeft') setDays((d) => Math.max(MIN, d - 1));
            if (e.key === 'ArrowRight') setDays((d) => Math.min(MAX, d + 1));
          }}
          className="relative mx-md mb-tight h-compact cursor-grab touch-none select-none outline-none"
        >
          <div className="absolute inset-x-0 top-1/2 h-meter -translate-y-1/2 overflow-hidden rounded-full bg-canvas">
            <div className="h-full rounded-full bg-primary" style={{ width: `${String(pct)}%` }} />
          </div>
          <div className="pointer-events-none absolute inset-x-0 top-1/2 h-meter -translate-y-1/2">
            {TICKS.map((i) => (
              <span
                key={i}
                className="absolute top-1/2 size-tick -translate-x-1/2 -translate-y-1/2 rounded-full bg-line-strong"
                style={{ left: `${String((i / (MAX - 1)) * 100)}%` }}
              />
            ))}
          </div>
          <div
            className="absolute top-1/2 size-glyph -translate-x-1/2 -translate-y-1/2 rounded-full border-[2px] border-primary bg-white shadow-thumb"
            style={{ left: `${String(pct)}%` }}
          />
        </div>
        <div className="mx-md mb-xl flex justify-between type-caption">
          <span>{t('warming.days.min')}</span>
          <span>{t('warming.days.max')}</span>
        </div>

        <SegmentedControl
          variant="outline"
          className="mb-2xl"
          // The presets are numbers and the control keys on strings, so the value it
          // carries is the number's own text; the handler puts the number back.
          value={String(days)}
          options={PRESETS.map((n) => ({
            value: String(n),
            label: `${String(n)} ${t('warming.days.label', { count: n })}`,
          }))}
          onChange={(value) => {
            setDays(Number(value));
          }}
        />

        <div className="mb-sm flex items-center gap-sm type-item-title">
          {t('warming.persona.label')}
          <span className="tb-tip inline-flex">
            <button
              type="button"
              aria-label={t('warming.persona.label')}
              aria-describedby={personaTipId}
              className="inline-flex size-glyph items-center justify-center rounded-full border border-line text-micro font-bold text-ink-subtle"
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
          className="mb-2xl"
          value={persona}
          ariaLabel={t('warming.persona.label')}
          options={PERSONAS.map((p) => ({
            value: p,
            label: (
              <>
                <div className="type-item-title">{t(`warming.persona.${p}.name`)}</div>
                <div className="mt-hair type-caption">{t(`warming.persona.${p}.hint`)}</div>
              </>
            ),
          }))}
          onChange={(p) => {
            setPersona(p);
          }}
        />

        <div className="flex justify-end gap-sm">
          <Button
            variant="primary"
            onClick={() => {
              onConfirm(days, persona);
              onClose();
            }}
          >
            {t('warming.days.start')}
          </Button>
          <Button onClick={onClose}>{t('warming.days.cancel')}</Button>
        </div>
      </div>
    </Modal>
  );
}
