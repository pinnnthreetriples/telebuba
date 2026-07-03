import { useMutation } from '@tanstack/react-query';
import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { spamCheckAccountMutation } from '@/entities/account';
import { Modal } from '@/shared/ui';

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
    <Modal onClose={onClose} z={72} className="w-[440px]">
      <div className="p-6">
        <div className="mb-1 flex items-start gap-[10px]">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-[10px] bg-[#e8f0ff] text-primary">
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
          <div className="flex-1 text-[16px] font-bold">{t('warming.days.title')}</div>
          <span className="tb-tip inline-flex shrink-0">
            <button
              type="button"
              disabled={spam === 'loading'}
              onClick={runSpamCheck}
              className={`inline-flex items-center gap-[6px] rounded-full border bg-white px-[11px] py-[6px] text-[12px] font-medium disabled:opacity-60 ${
                spam === 'clean'
                  ? 'border-success text-success'
                  : spam === 'limited'
                    ? 'border-danger text-danger'
                    : 'border-line-input text-ink-muted'
              }`}
            >
              <svg
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z" />
                <path d="m9 12 2 2 4-4" />
              </svg>
              {spam === 'loading'
                ? t('warming.days.spamChecking')
                : spam === 'clean'
                  ? t('warming.days.spamClean')
                  : spam === 'limited'
                    ? t('warming.days.spamLimited')
                    : t('warming.days.spamCheck')}
            </button>
            <span className="tb-tip-pop">{t('warming.days.spamTip')}</span>
          </span>
        </div>
        <div className="mb-[22px] text-[13px] text-ink-muted">
          {t('warming.days.subtitle', { phone })}
        </div>

        <div className="mb-[18px] text-center">
          <div className="text-[42px] font-bold leading-none tracking-[-0.02em] text-primary">
            {days}
          </div>
          <div className="mt-1 text-[13px] text-ink-muted">
            {t('warming.days.label', { count: days })}
          </div>
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
          className="relative mx-[11px] mb-[6px] h-[30px] cursor-grab touch-none select-none outline-none"
        >
          <div className="absolute inset-x-0 top-1/2 h-[6px] -translate-y-1/2 overflow-hidden rounded-full bg-[#eeedea]">
            <div className="h-full rounded-full bg-primary" style={{ width: `${String(pct)}%` }} />
          </div>
          <div className="pointer-events-none absolute inset-x-0 top-1/2 h-[6px] -translate-y-1/2">
            {TICKS.map((i) => (
              <span
                key={i}
                className="absolute top-1/2 h-[3px] w-[3px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-[rgba(11,11,12,0.18)]"
                style={{ left: `${String((i / (MAX - 1)) * 100)}%` }}
              />
            ))}
          </div>
          <div
            className="absolute top-1/2 h-[18px] w-[18px] -translate-x-1/2 -translate-y-1/2 rounded-full border-[2px] border-primary bg-white shadow-[0_1px_4px_rgba(0,0,0,0.2)]"
            style={{ left: `${String(pct)}%` }}
          />
        </div>
        <div className="mx-[11px] mb-[18px] flex justify-between text-[11px] text-ink-subtle">
          <span>{t('warming.days.min')}</span>
          <span>{t('warming.days.max')}</span>
        </div>

        <div className="mb-[22px] flex gap-[6px]">
          {PRESETS.map((n) => (
            <button
              key={n}
              type="button"
              onClick={() => {
                setDays(n);
              }}
              className={`flex-1 rounded-[10px] border py-2 text-[12.5px] font-medium transition-colors ${
                days === n
                  ? 'border-primary bg-primary-tint text-primary'
                  : 'border-line-input bg-white text-ink-muted hover:bg-[#f7f6f4]'
              }`}
            >
              {String(n)} {t('warming.days.label', { count: n })}
            </button>
          ))}
        </div>

        <div className="mb-[8px] flex items-center gap-[6px] text-[12.5px] font-semibold">
          {t('warming.persona.label')}
          <span className="tb-tip inline-flex">
            <button
              type="button"
              aria-label={t('warming.persona.label')}
              className="inline-flex h-[16px] w-[16px] items-center justify-center rounded-full border border-line-input text-[10px] font-bold text-ink-subtle"
            >
              ?
            </button>
            <span className="tb-tip-pop">{t('warming.persona.tip')}</span>
          </span>
        </div>
        <div className="mb-[22px] flex gap-[6px]">
          {PERSONAS.map((p) => (
            <button
              key={p}
              type="button"
              onClick={() => {
                setPersona(p);
              }}
              className={`flex-1 rounded-[10px] border px-2 py-[9px] text-center transition-colors ${
                persona === p
                  ? 'border-primary bg-primary-tint text-primary'
                  : 'border-line-input bg-white text-ink-muted hover:bg-[#f7f6f4]'
              }`}
            >
              <div className="text-[12.5px] font-semibold">{t(`warming.persona.${p}.name`)}</div>
              <div className="mt-[2px] text-[11px] text-ink-subtle">
                {t(`warming.persona.${p}.hint`)}
              </div>
            </button>
          ))}
        </div>

        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={() => {
              onConfirm(days, persona);
              onClose();
            }}
            className="rounded-full bg-primary px-[22px] py-[9px] text-[13px] font-semibold text-white transition-colors hover:bg-[#0057db]"
          >
            {t('warming.days.start')}
          </button>
          <button
            type="button"
            onClick={onClose}
            className="rounded-full border border-line-input bg-white px-[18px] py-[9px] text-[13px] font-medium text-ink"
          >
            {t('warming.days.cancel')}
          </button>
        </div>
      </div>
    </Modal>
  );
}
