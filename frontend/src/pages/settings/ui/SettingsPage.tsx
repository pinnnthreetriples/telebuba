import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { type ReactNode, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  neurocommentSettingsQueryOptions,
  updateNeurocommentSettingsMutation,
} from '@/entities/campaign';
import { updateWarmingSettingsMutation, warmingSettingsQueryOptions } from '@/entities/warming';
import type { NeurocommentSettings, WarmingSettings } from '@/shared/api';

const INPUT =
  'tb-time w-full rounded-[10px] border border-line-input bg-white px-3 py-[9px] text-[13px] outline-none';
const FIELD_LABEL = 'mb-[6px] block text-[12px] font-medium text-[#3a3a3a]';

// Warming per-action counts are auto-managed by the engine (phase + trust), not
// operator-set — shown read-only for reference (the editable warming controls
// are the toggles below). Neuro limits ARE real + editable (loaded from the API).
const WARM_LIMITS = { sub: '15', read: '80', react: '25', pauseFrom: '3', pauseTo: '12' };
// The three real, engine-used warming toggles surfaced as the design's flag rows.
const WARMING_TOGGLES = ['reactions_enabled', 'join_enabled', 'inter_account_chat'] as const;
type WarmingToggle = (typeof WARMING_TOGGLES)[number];

function neuroForm(s: NeurocommentSettings) {
  return {
    cpd: String(s.max_comments_per_channel_per_day),
    delayFrom: String(s.reply_delay_min_seconds),
    delayTo: String(s.reply_delay_max_seconds),
    parallel: String(s.max_comments_per_hour),
    trust: String(s.min_trust_score),
  };
}

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
  mb = 'mb-[14px]',
  children,
}: {
  title?: string;
  subtitle?: string;
  className?: string;
  mb?: string;
  children: ReactNode;
}) {
  return (
    <div className={`${mb} rounded-2xl border border-line bg-white ${className}`}>
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
  readOnly = false,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  readOnly?: boolean;
}) {
  return (
    <label className="block">
      <span className={FIELD_LABEL}>{label}</span>
      <input
        inputMode="numeric"
        value={value}
        readOnly={readOnly}
        disabled={readOnly}
        onChange={(event) => {
          onChange(event.target.value);
        }}
        className={readOnly ? `${INPUT} cursor-not-allowed bg-[#f6f5f2] text-ink-subtle` : INPUT}
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
  readOnly = false,
}: {
  label: string;
  from: string;
  to: string;
  onFrom: (value: string) => void;
  onTo: (value: string) => void;
  readOnly?: boolean;
}) {
  const { t } = useTranslation();
  const box = `tb-time flex min-w-0 flex-1 items-center gap-[7px] rounded-[10px] border border-line-input px-3 py-[9px] ${readOnly ? 'bg-[#f6f5f2]' : 'bg-white'}`;
  const inp = `min-w-0 flex-1 border-none bg-transparent text-right text-[13px] outline-none ${readOnly ? 'text-ink-subtle' : ''}`;
  return (
    <div className="min-w-0">
      <span className={FIELD_LABEL}>{label}</span>
      <div className="flex items-center gap-[9px]">
        <label className={box}>
          <span className="shrink-0 text-[11px] text-ink-subtle">{t('settings.range.from')}</span>
          <input
            inputMode="numeric"
            value={from}
            readOnly={readOnly}
            disabled={readOnly}
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
            readOnly={readOnly}
            disabled={readOnly}
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

function SettingsForm({
  settings,
  neuroSettings,
}: {
  settings: WarmingSettings;
  neuroSettings: NeurocommentSettings;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const saveWarm = useMutation(updateWarmingSettingsMutation());
  const saveNeuro = useMutation(updateNeurocommentSettingsMutation());

  const [geminiKey, setGeminiKey] = useState('');
  const [showKey, setShowKey] = useState(false);
  const [neuro, setNeuro] = useState(() => neuroForm(neuroSettings));
  const [toggles, setToggles] = useState<Record<WarmingToggle, boolean>>({
    reactions_enabled: settings.reactions_enabled ?? true,
    join_enabled: settings.join_enabled ?? true,
    inter_account_chat: settings.inter_account_chat ?? false,
  });
  const [justSaved, setJustSaved] = useState(false);
  const [saveFailed, setSaveFailed] = useState(false);

  // Re-sync the neuro form if the server value changes (e.g. another tab saved).
  useEffect(() => {
    setNeuro(neuroForm(neuroSettings));
  }, [neuroSettings]);

  const pending = saveWarm.isPending || saveNeuro.isPending;

  const onSave = () => {
    setSaveFailed(false);
    void Promise.all([
      saveWarm.mutateAsync({
        body: {
          reactions_enabled: toggles.reactions_enabled,
          join_enabled: toggles.join_enabled,
          inter_account_chat: toggles.inter_account_chat,
          enforce_readiness: settings.enforce_readiness ?? true,
          max_daily_actions: 0,
          gemini_model: settings.gemini_model,
          gemini_api_key: geminiKey.trim() === '' ? null : geminiKey,
          clear_gemini_key: false,
        },
      }),
      saveNeuro.mutateAsync({
        body: {
          max_comments_per_channel_per_day: Number(neuro.cpd),
          reply_delay_min_seconds: Number(neuro.delayFrom),
          reply_delay_max_seconds: Number(neuro.delayTo),
          max_comments_per_hour: Number(neuro.parallel),
          min_trust_score: Number(neuro.trust),
        },
      }),
    ]).then(
      () => {
        setJustSaved(true);
        window.setTimeout(() => {
          setJustSaved(false);
        }, 1400);
        void queryClient.invalidateQueries();
      },
      () => {
        setSaveFailed(true);
        window.setTimeout(() => {
          setSaveFailed(false);
        }, 2400);
      },
    );
  };

  const onCancel = () => {
    setGeminiKey('');
    setNeuro(neuroForm(neuroSettings));
    setToggles({
      reactions_enabled: settings.reactions_enabled ?? true,
      join_enabled: settings.join_enabled ?? true,
      inter_account_chat: settings.inter_account_chat ?? false,
    });
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
              className="flex w-[42px] items-center justify-center rounded-[10px] border border-line-input bg-white text-ink-muted transition-colors hover:border-[#cbd7ec] hover:bg-[#f2f6ff] hover:text-primary"
            >
              {showKey ? (
                <svg
                  width="17"
                  height="17"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.8"
                >
                  <path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 10 8 10 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24" />
                  <path d="M1 1l22 22" />
                  <path d="M6.61 6.61A13.5 13.5 0 0 0 2 12s3 8 10 8a9.7 9.7 0 0 0 5.39-1.61" />
                </svg>
              ) : (
                <svg
                  width="17"
                  height="17"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.8"
                >
                  <path d="M2 12s3-8 10-8 10 8 10 8-3 8-10 8-10-8-10-8z" />
                  <circle cx="12" cy="12" r="3" />
                </svg>
              )}
            </button>
          </div>
        </label>
      </Card>

      <Card title={t('settings.warmLimits.title')} subtitle={t('settings.warmLimits.subtitle')}>
        <div className="mb-3 text-[11.5px] text-ink-subtle">{t('settings.warmLimits.auto')}</div>
        <div className="grid grid-cols-2 gap-3">
          <NumberField
            label={t('settings.warmLimits.sub')}
            value={WARM_LIMITS.sub}
            onChange={() => undefined}
            readOnly
          />
          <NumberField
            label={t('settings.warmLimits.read')}
            value={WARM_LIMITS.read}
            onChange={() => undefined}
            readOnly
          />
          <NumberField
            label={t('settings.warmLimits.react')}
            value={WARM_LIMITS.react}
            onChange={() => undefined}
            readOnly
          />
          <RangeField
            label={t('settings.warmLimits.pause')}
            from={WARM_LIMITS.pauseFrom}
            to={WARM_LIMITS.pauseTo}
            onFrom={() => undefined}
            onTo={() => undefined}
            readOnly
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

      <Card className="px-5 py-[6px]" mb="mb-[18px]">
        {WARMING_TOGGLES.map((flag) => (
          <div
            key={flag}
            className="flex items-center justify-between gap-3 border-b border-[#f0eeeb] py-[13px]"
          >
            <div>
              <div className="text-[13px] font-medium">{t(`settings.flag.${flag}.label`)}</div>
              <div className="mt-px text-[11.5px] text-ink-subtle">
                {t(`settings.flag.${flag}.desc`)}
              </div>
            </div>
            <Switch
              checked={toggles[flag]}
              onChange={(v) => {
                setToggles((f) => ({ ...f, [flag]: v }));
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
          className="rounded-full border border-line-input bg-white px-[18px] py-[9px] text-[13px] font-medium"
        >
          {t('settings.cancel')}
        </button>
        <button
          type="button"
          onClick={onSave}
          disabled={pending}
          className={`rounded-full px-[22px] py-[9px] text-[13px] font-medium text-white transition-colors disabled:opacity-60 ${justSaved ? 'bg-[#2e9e64]' : saveFailed ? 'bg-danger' : 'bg-primary'}`}
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
          ) : saveFailed ? (
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
                  <path d="M18 6 6 18M6 6l12 12" />
                </svg>
              </span>
              <span className="tb-swapin inline-block" style={{ animationDelay: '0.09s' }}>
                {t('settings.saveFailed')}
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
  const warming = useQuery(warmingSettingsQueryOptions());
  const neuro = useQuery(neurocommentSettingsQueryOptions());

  const loading = warming.isPending || neuro.isPending;
  const failed = warming.isError || neuro.isError || !warming.data || !neuro.data;

  return (
    <div className="tb-fadeup mx-auto max-w-[760px]">
      <h1 className="m-0 mb-[18px] text-[22px] font-bold tracking-[-0.02em]">
        {t('settings.title')}
      </h1>
      {loading ? (
        <p className="text-ink-muted">{t('settings.loading')}</p>
      ) : failed ? (
        <p role="alert" className="text-danger">
          {t('settings.error')}
        </p>
      ) : (
        <SettingsForm settings={warming.data} neuroSettings={neuro.data} />
      )}
    </div>
  );
}
