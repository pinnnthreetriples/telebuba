import { useForm, useStore } from '@tanstack/react-form';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { type ReactNode, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  neurocommentSettingsQueryOptions,
  updateNeurocommentSettingsMutation,
} from '@/entities/campaign';
import { updateWarmingSettingsMutation, warmingSettingsQueryOptions } from '@/entities/warming';
import type { NeurocommentSettings, WarmingSettings } from '@/shared/api';
import { FieldError, FormField } from '@/shared/ui';

import { neuroFormSchema, neuroFormValue, neuroUpdateBody } from './neuroSettingsForm';

const INPUT =
  'tb-time w-full rounded-[10px] border border-line-input bg-white px-3 py-[9px] text-[13px] outline-none';
const FIELD_LABEL = 'mb-[6px] block text-[12px] font-medium text-[#3a3a3a]';

// The three real, engine-used warming toggles surfaced as the design's flag rows.
const WARMING_TOGGLES = ['reactions_enabled', 'join_enabled', 'inter_account_chat'] as const;
type WarmingToggle = (typeof WARMING_TOGGLES)[number];

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

function EyeIcon({ off }: { off: boolean }) {
  return off ? (
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
  );
}

// One masked API-key input (Gemini or OpenAI): password field + show/hide toggle
// + a "clear stored key" affordance. Blank = keep; clear = wipe the stored key.
function ApiKeyField({
  label,
  value,
  show,
  keySet,
  placeholder,
  toggleLabel,
  clearLabel,
  onChange,
  onToggleShow,
  onClear,
}: {
  label: string;
  value: string;
  show: boolean;
  keySet: boolean;
  placeholder: string;
  toggleLabel: string;
  clearLabel: string;
  onChange: (value: string) => void;
  onToggleShow: () => void;
  onClear: () => void;
}) {
  return (
    <label className="block">
      <span className={FIELD_LABEL}>{label}</span>
      <div className="flex gap-2">
        <input
          type={show ? 'text' : 'password'}
          value={value}
          onChange={(event) => {
            onChange(event.target.value);
          }}
          placeholder={placeholder}
          className={`${INPUT} flex-1 font-mono`}
        />
        <button
          type="button"
          aria-label={toggleLabel}
          onClick={onToggleShow}
          className="flex w-[42px] items-center justify-center rounded-[10px] border border-line-input bg-white text-ink-muted transition-colors hover:border-[#cbd7ec] hover:bg-[#f2f6ff] hover:text-primary"
        >
          <EyeIcon off={show} />
        </button>
      </div>
      {keySet && (
        <button
          type="button"
          onClick={onClear}
          className="mt-[9px] text-[12px] font-medium text-danger transition-colors hover:underline"
        >
          {clearLabel}
        </button>
      )}
    </label>
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
  // Tracks a pending "clear the stored key" action (distinct from "leave blank to
  // keep"). Sends clear_gemini_key: true on the next save.
  const [clearKey, setClearKey] = useState(false);
  const [openaiKey, setOpenaiKey] = useState('');
  const [showOpenaiKey, setShowOpenaiKey] = useState(false);
  const [clearOpenaiKey, setClearOpenaiKey] = useState(false);
  const [provider, setProvider] = useState<'gemini' | 'openai'>(
    settings.captcha_llm_provider ?? 'gemini',
  );
  const [toggles, setToggles] = useState<Record<WarmingToggle, boolean>>({
    reactions_enabled: settings.reactions_enabled ?? true,
    join_enabled: settings.join_enabled ?? true,
    inter_account_chat: settings.inter_account_chat ?? false,
  });
  const [justSaved, setJustSaved] = useState(false);
  const [saveFailed, setSaveFailed] = useState(false);

  const form = useForm({
    defaultValues: neuroFormValue(neuroSettings),
    validators: { onChange: neuroFormSchema, onMount: neuroFormSchema },
    onSubmit: async ({ value }) => {
      setSaveFailed(false);
      try {
        await Promise.all([
          saveWarm.mutateAsync({
            body: {
              reactions_enabled: toggles.reactions_enabled,
              join_enabled: toggles.join_enabled,
              inter_account_chat: toggles.inter_account_chat,
              enforce_readiness: settings.enforce_readiness ?? true,
              max_daily_actions: 0,
              gemini_model: settings.gemini_model,
              // clear wins over a typed key; a typed key sets it; blank keeps it.
              gemini_api_key: clearKey ? null : geminiKey.trim() === '' ? null : geminiKey,
              clear_gemini_key: clearKey,
              openai_api_key: clearOpenaiKey ? null : openaiKey.trim() === '' ? null : openaiKey,
              clear_openai_key: clearOpenaiKey,
              openai_model: settings.openai_model,
              captcha_llm_provider: provider,
            },
          }),
          saveNeuro.mutateAsync({ body: neuroUpdateBody(value) }),
        ]);
        setGeminiKey('');
        setClearKey(false);
        setOpenaiKey('');
        setClearOpenaiKey(false);
        setJustSaved(true);
        window.setTimeout(() => {
          setJustSaved(false);
        }, 1400);
        void queryClient.invalidateQueries({
          queryKey: warmingSettingsQueryOptions().queryKey,
        });
        void queryClient.invalidateQueries({
          queryKey: neurocommentSettingsQueryOptions().queryKey,
        });
      } catch {
        setSaveFailed(true);
        window.setTimeout(() => {
          setSaveFailed(false);
        }, 2400);
      }
    },
  });

  const canSubmit = useStore(form.store, (state) => state.canSubmit);

  // Re-sync the neuro form if the server value changes (e.g. another tab saved).
  useEffect(() => {
    form.reset(neuroFormValue(neuroSettings));
  }, [neuroSettings, form]);

  const pending = saveWarm.isPending || saveNeuro.isPending;
  // The stored key is present unless the operator just chose to clear it.
  const keySet = (settings.has_gemini_key ?? false) && !clearKey;
  const openaiKeySet = (settings.has_openai_key ?? false) && !clearOpenaiKey;

  const onCancel = () => {
    setGeminiKey('');
    setClearKey(false);
    setOpenaiKey('');
    setClearOpenaiKey(false);
    setProvider(settings.captcha_llm_provider ?? 'gemini');
    form.reset(neuroFormValue(neuroSettings));
    setToggles({
      reactions_enabled: settings.reactions_enabled ?? true,
      join_enabled: settings.join_enabled ?? true,
      inter_account_chat: settings.inter_account_chat ?? false,
    });
  };

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        void form.handleSubmit();
      }}
    >
      <Card title={t('settings.api.title')} subtitle={t('settings.api.subtitle')}>
        <div className="space-y-4">
          <ApiKeyField
            label={t('settings.api.geminiKey')}
            value={geminiKey}
            show={showKey}
            keySet={keySet}
            placeholder={
              clearKey
                ? t('settings.api.keyCleared')
                : keySet
                  ? t('settings.api.keySet')
                  : t('settings.api.keyUnset')
            }
            toggleLabel={t('settings.api.toggleVisibility')}
            clearLabel={t('settings.api.clearKey')}
            onChange={(value) => {
              setGeminiKey(value);
              if (clearKey) setClearKey(false);
            }}
            onToggleShow={() => {
              setShowKey((value) => !value);
            }}
            onClear={() => {
              setClearKey(true);
              setGeminiKey('');
            }}
          />
          <ApiKeyField
            label={t('settings.api.openaiKey')}
            value={openaiKey}
            show={showOpenaiKey}
            keySet={openaiKeySet}
            placeholder={
              clearOpenaiKey
                ? t('settings.api.keyCleared')
                : openaiKeySet
                  ? t('settings.api.keySet')
                  : t('settings.api.keyUnset')
            }
            toggleLabel={t('settings.api.toggleVisibility')}
            clearLabel={t('settings.api.clearKey')}
            onChange={(value) => {
              setOpenaiKey(value);
              if (clearOpenaiKey) setClearOpenaiKey(false);
            }}
            onToggleShow={() => {
              setShowOpenaiKey((value) => !value);
            }}
            onClear={() => {
              setClearOpenaiKey(true);
              setOpenaiKey('');
            }}
          />
        </div>
      </Card>

      <Card title={t('settings.captchaLlm.title')} subtitle={t('settings.captchaLlm.subtitle')}>
        <div className="flex gap-2">
          {(['gemini', 'openai'] as const).map((option) => (
            <button
              key={option}
              type="button"
              aria-pressed={provider === option}
              onClick={() => {
                setProvider(option);
              }}
              className={`flex-1 rounded-[10px] border px-3 py-[9px] text-[13px] font-medium transition-colors ${
                provider === option
                  ? 'border-primary bg-[#f2f6ff] text-primary'
                  : 'border-line-input bg-white text-ink-muted hover:border-[#c8c6c2] hover:bg-[#f7f6f4]'
              }`}
            >
              {t(`settings.captchaLlm.${option}`)}
            </button>
          ))}
        </div>
      </Card>

      <Card title={t('settings.warmLimits.title')} subtitle={t('settings.warmLimits.subtitle')}>
        <div className="rounded-[10px] border border-dashed border-line-input bg-[#faf9f7] px-4 py-3 text-[12px] leading-relaxed text-ink-subtle">
          {t('settings.warmLimits.engineNote')}
        </div>
      </Card>

      <Card title={t('settings.neuroLimits.title')} subtitle={t('settings.neuroLimits.subtitle')}>
        <div className="grid grid-cols-2 gap-3">
          <form.Field name="cpd">
            {(field) => (
              <FormField field={field} label={t('settings.neuroLimits.cpd')} inputMode="numeric" />
            )}
          </form.Field>
          <div className="min-w-0">
            <span className={FIELD_LABEL}>{t('settings.neuroLimits.delay')}</span>
            <div className="flex items-center gap-[9px]">
              <form.Field name="delayFrom">
                {(field) => (
                  <label className="tb-time flex min-w-0 flex-1 items-center gap-[7px] rounded-[10px] border border-line-input bg-white px-3 py-[9px]">
                    <span className="shrink-0 text-[11px] text-ink-subtle">
                      {t('settings.range.from')}
                    </span>
                    <input
                      inputMode="numeric"
                      value={field.state.value}
                      onChange={(event) => {
                        field.handleChange(event.target.value);
                      }}
                      onBlur={field.handleBlur}
                      aria-label={t('settings.neuroLimits.delayFrom')}
                      className="min-w-0 flex-1 border-none bg-transparent text-right text-[13px] outline-none"
                    />
                  </label>
                )}
              </form.Field>
              <form.Field name="delayTo">
                {(field) => (
                  <label className="tb-time flex min-w-0 flex-1 items-center gap-[7px] rounded-[10px] border border-line-input bg-white px-3 py-[9px]">
                    <span className="shrink-0 text-[11px] text-ink-subtle">
                      {t('settings.range.to')}
                    </span>
                    <input
                      inputMode="numeric"
                      value={field.state.value}
                      onChange={(event) => {
                        field.handleChange(event.target.value);
                      }}
                      onBlur={field.handleBlur}
                      aria-label={t('settings.neuroLimits.delayTo')}
                      className="min-w-0 flex-1 border-none bg-transparent text-right text-[13px] outline-none"
                    />
                  </label>
                )}
              </form.Field>
            </div>
            <form.Field name="delayTo">{(field) => <FieldError field={field} />}</form.Field>
          </div>
          <form.Field name="parallel">
            {(field) => (
              <FormField
                field={field}
                label={t('settings.neuroLimits.parallel')}
                inputMode="numeric"
              />
            )}
          </form.Field>
          <form.Field name="trust">
            {(field) => (
              <FormField
                field={field}
                label={t('settings.neuroLimits.trust')}
                inputMode="numeric"
              />
            )}
          </form.Field>
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
          type="submit"
          disabled={pending || !canSubmit}
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
    </form>
  );
}

export function SettingsPage() {
  const { t } = useTranslation();
  const warming = useQuery(warmingSettingsQueryOptions());
  const neuro = useQuery(neurocommentSettingsQueryOptions());

  const loading = warming.isPending || neuro.isPending;
  const failed = warming.isError || neuro.isError || !warming.data || !neuro.data;

  return (
    <div className="tb-fadeup max-w-[760px]">
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
