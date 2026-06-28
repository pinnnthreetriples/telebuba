import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { type ReactNode, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { updateWarmingSettingsMutation, warmingSettingsQueryOptions } from '@/entities/warming';
import type { WarmingSettings } from '@/shared/api';

const INPUT =
  'tb-time w-full rounded-[10px] border border-line bg-white px-3 py-[9px] text-[13px] outline-none';
const FIELD_LABEL = 'mb-[6px] block text-[12px] font-medium text-[#3a3a3a]';

// ponytail: limits + flags are local mock until the backend carries them — the
// page is design-first; only the Gemini key persists (the field that maps 1:1).
const WARM_LIMITS = { sub: '15', read: '80', react: '25', pauseFrom: '3', pauseTo: '12' };
const NEURO_LIMITS = { cpd: '20', delayFrom: '8', delayTo: '25', parallel: '6', trust: '45' };
const FLAGS = ['autostart', 'notifyErr', 'antidetect'] as const;

// The design's pill switch (track + sliding thumb), 18px of travel.
function Switch({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  label: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={() => {
        onChange(!checked);
      }}
      className={`tb-sw relative h-[26px] w-[44px] shrink-0 rounded-full transition-colors ${checked ? 'bg-primary' : 'bg-[#cbc9c4]'}`}
    >
      <span
        className={`tb-sw-thumb absolute top-[3px] block h-5 w-5 rounded-full bg-white shadow-[0_1px_3px_rgba(0,0,0,0.3)] transition-transform ${checked ? 'translate-x-[21px]' : 'translate-x-[3px]'}`}
      />
    </button>
  );
}

function Card({
  title,
  subtitle,
  className = 'px-5 py-[18px]',
  children,
}: {
  title?: string;
  subtitle?: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div className={`mb-[14px] rounded-2xl border border-line bg-white ${className}`}>
      {title ? <div className="mb-[3px] text-[13px] font-semibold">{title}</div> : null}
      {subtitle ? <div className="mb-4 text-[12px] text-ink-subtle">{subtitle}</div> : null}
      {children}
    </div>
  );
}

function NumberField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="block">
      <span className={FIELD_LABEL}>{label}</span>
      <input
        inputMode="numeric"
        value={value}
        onChange={(event) => {
          onChange(event.target.value);
        }}
        className={INPUT}
      />
    </label>
  );
}

function RangeField({
  label,
  from,
  to,
  onFrom,
  onTo,
}: {
  label: string;
  from: string;
  to: string;
  onFrom: (value: string) => void;
  onTo: (value: string) => void;
}) {
  const { t } = useTranslation();
  const box =
    'tb-time flex flex-1 items-center gap-[7px] rounded-[10px] border border-line bg-white px-3 py-[9px]';
  const inp = 'min-w-0 flex-1 border-none bg-transparent text-right text-[13px] outline-none';
  return (
    <div>
      <span className={FIELD_LABEL}>{label}</span>
      <div className="flex items-center gap-[9px]">
        <label className={box}>
          <span className="shrink-0 text-[11px] text-ink-subtle">{t('settings.range.from')}</span>
          <input
            inputMode="numeric"
            value={from}
            onChange={(event) => {
              onFrom(event.target.value);
            }}
            className={inp}
          />
        </label>
        <label className={box}>
          <span className="shrink-0 text-[11px] text-ink-subtle">{t('settings.range.to')}</span>
          <input
            inputMode="numeric"
            value={to}
            onChange={(event) => {
              onTo(event.target.value);
            }}
            className={inp}
          />
        </label>
      </div>
    </div>
  );
}

function SettingsForm({ settings }: { settings: WarmingSettings }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const save = useMutation(updateWarmingSettingsMutation());

  const [geminiKey, setGeminiKey] = useState('');
  const [showKey, setShowKey] = useState(false);
  const [warm, setWarm] = useState(WARM_LIMITS);
  const [neuro, setNeuro] = useState(NEURO_LIMITS);
  const [flags, setFlags] = useState({ autostart: true, notifyErr: true, antidetect: true });
  const [justSaved, setJustSaved] = useState(false);

  const onSave = () => {
    save.mutate(
      {
        body: {
          reactions_enabled: settings.reactions_enabled ?? true,
          join_enabled: settings.join_enabled ?? true,
          inter_account_chat: settings.inter_account_chat ?? false,
          enforce_readiness: settings.enforce_readiness ?? true,
          quiet_hours_enabled: settings.quiet_hours_enabled ?? false,
          quiet_hours_start: settings.quiet_hours_start ?? 0,
          quiet_hours_end: settings.quiet_hours_end ?? 0,
          max_daily_actions: 0,
          gemini_model: settings.gemini_model,
          gemini_api_key: geminiKey.trim() === '' ? null : geminiKey,
          clear_gemini_key: false,
        },
      },
      {
        onSuccess: () => {
          setJustSaved(true);
          window.setTimeout(() => {
            setJustSaved(false);
          }, 1400);
          void queryClient.invalidateQueries();
        },
      },
    );
  };

  const onCancel = () => {
    setGeminiKey('');
    setWarm(WARM_LIMITS);
    setNeuro(NEURO_LIMITS);
    setFlags({ autostart: true, notifyErr: true, antidetect: true });
  };

  return (
    <>
      <Card title={t('settings.api.title')} subtitle={t('settings.api.subtitle')}>
        <label className="block">
          <span className={FIELD_LABEL}>{t('settings.api.geminiKey')}</span>
          <div className="flex gap-2">
            <input
              type={showKey ? 'text' : 'password'}
              value={geminiKey}
              onChange={(event) => {
                setGeminiKey(event.target.value);
              }}
              placeholder={
                settings.has_gemini_key ? t('settings.api.keySet') : t('settings.api.keyUnset')
              }
              className={`${INPUT} flex-1 font-mono`}
            />
            <button
              type="button"
              aria-label={t('settings.api.geminiKey')}
              onClick={() => {
                setShowKey((value) => !value);
              }}
              className="flex w-[42px] items-center justify-center rounded-[10px] border border-line bg-white text-ink-muted transition-colors hover:border-[#cbd7ec] hover:bg-[#f2f6ff] hover:text-primary"
            >
              {showKey ? (
                <svg
                  width="16"
                  height="16"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.8"
                >
                  <path d="M9.88 9.88a3 3 0 1 0 4.24 4.24" />
                  <path d="M10.73 5.08A10.43 10.43 0 0 1 12 5c7 0 10 7 10 7a13.16 13.16 0 0 1-1.67 2.68" />
                  <path d="M6.61 6.61A13.526 13.526 0 0 0 2 12s3 7 10 7a9.74 9.74 0 0 0 5.39-1.61" />
                  <path d="m2 2 20 20" />
                </svg>
              ) : (
                <svg
                  width="16"
                  height="16"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.8"
                >
                  <path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z" />
                  <circle cx="12" cy="12" r="3" />
                </svg>
              )}
            </button>
          </div>
        </label>
      </Card>

      <Card title={t('settings.warmLimits.title')} subtitle={t('settings.warmLimits.subtitle')}>
        <div className="grid grid-cols-2 gap-3">
          <NumberField
            label={t('settings.warmLimits.sub')}
            value={warm.sub}
            onChange={(v) => {
              setWarm((w) => ({ ...w, sub: v }));
            }}
          />
          <NumberField
            label={t('settings.warmLimits.read')}
            value={warm.read}
            onChange={(v) => {
              setWarm((w) => ({ ...w, read: v }));
            }}
          />
          <NumberField
            label={t('settings.warmLimits.react')}
            value={warm.react}
            onChange={(v) => {
              setWarm((w) => ({ ...w, react: v }));
            }}
          />
          <RangeField
            label={t('settings.warmLimits.pause')}
            from={warm.pauseFrom}
            to={warm.pauseTo}
            onFrom={(v) => {
              setWarm((w) => ({ ...w, pauseFrom: v }));
            }}
            onTo={(v) => {
              setWarm((w) => ({ ...w, pauseTo: v }));
            }}
          />
        </div>
      </Card>

      <Card title={t('settings.neuroLimits.title')} subtitle={t('settings.neuroLimits.subtitle')}>
        <div className="grid grid-cols-2 gap-3">
          <NumberField
            label={t('settings.neuroLimits.cpd')}
            value={neuro.cpd}
            onChange={(v) => {
              setNeuro((n) => ({ ...n, cpd: v }));
            }}
          />
          <RangeField
            label={t('settings.neuroLimits.delay')}
            from={neuro.delayFrom}
            to={neuro.delayTo}
            onFrom={(v) => {
              setNeuro((n) => ({ ...n, delayFrom: v }));
            }}
            onTo={(v) => {
              setNeuro((n) => ({ ...n, delayTo: v }));
            }}
          />
          <NumberField
            label={t('settings.neuroLimits.parallel')}
            value={neuro.parallel}
            onChange={(v) => {
              setNeuro((n) => ({ ...n, parallel: v }));
            }}
          />
          <NumberField
            label={t('settings.neuroLimits.trust')}
            value={neuro.trust}
            onChange={(v) => {
              setNeuro((n) => ({ ...n, trust: v }));
            }}
          />
        </div>
      </Card>

      <Card className="px-5 py-[6px]">
        {FLAGS.map((flag, index) => (
          <div
            key={flag}
            className={`flex items-center justify-between gap-3 py-[13px] ${index < FLAGS.length - 1 ? 'border-b border-[#f0eeeb]' : ''}`}
          >
            <div>
              <div className="text-[13px] font-medium">{t(`settings.flag.${flag}.label`)}</div>
              <div className="mt-px text-[11.5px] text-ink-subtle">
                {t(`settings.flag.${flag}.desc`)}
              </div>
            </div>
            <Switch
              checked={flags[flag]}
              onChange={(v) => {
                setFlags((f) => ({ ...f, [flag]: v }));
              }}
              label={t(`settings.flag.${flag}.label`)}
            />
          </div>
        ))}
      </Card>

      <div className="flex justify-end gap-2">
        <button
          type="button"
          onClick={onCancel}
          className="rounded-full border border-line bg-white px-[18px] py-[9px] text-[13px] font-medium"
        >
          {t('settings.cancel')}
        </button>
        <button
          type="button"
          onClick={onSave}
          disabled={save.isPending}
          className={`rounded-full px-[22px] py-[9px] text-[13px] font-medium text-white transition-colors disabled:opacity-60 ${justSaved ? 'bg-[#2e9e64]' : 'bg-primary'}`}
        >
          {justSaved ? (
            <span className="inline-flex items-center gap-[6px]">
              <span className="tb-swapin inline-flex">
                <svg
                  width="15"
                  height="15"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.4"
                >
                  <path d="M20 6 9 17l-5-5" />
                </svg>
              </span>
              <span className="tb-swapin inline-block" style={{ animationDelay: '0.09s' }}>
                {t('settings.saved')}
              </span>
            </span>
          ) : (
            t('settings.save')
          )}
        </button>
      </div>
    </>
  );
}

export function SettingsPage() {
  const { t } = useTranslation();
  const { data, isPending, isError } = useQuery(warmingSettingsQueryOptions());

  return (
    <div className="tb-fadeup mx-auto max-w-[760px]">
      <h1 className="m-0 mb-[18px] text-[22px] font-bold tracking-[-0.02em]">
        {t('settings.title')}
      </h1>
      {isPending ? (
        <p className="text-ink-muted">{t('settings.loading')}</p>
      ) : isError || !data ? (
        <p role="alert" className="text-danger">
          {t('settings.error')}
        </p>
      ) : (
        <SettingsForm settings={data} />
      )}
    </div>
  );
}
