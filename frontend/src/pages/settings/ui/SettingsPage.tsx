import { useForm, useStore } from '@tanstack/react-form';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  neurocommentSettingsQueryOptions,
  updateNeurocommentSettingsMutation,
} from '@/entities/campaign';
import { updateWarmingSettingsMutation, warmingSettingsQueryOptions } from '@/entities/warming';
import type { NeurocommentSettings, WarmingSettings } from '@/shared/api';
import {
  Button,
  Card,
  FieldError,
  FormField,
  HelpHint,
  Icon,
  Input,
  SegmentedControl,
  Switch,
} from '@/shared/ui';

import { ApiKeyField } from './ApiKeyField';
import { neuroFormSchema, neuroFormValue, neuroUpdateBody } from './neuroSettingsForm';

const FIELD_LABEL = 'mb-tight block type-label';

// The three real, engine-used warming toggles surfaced as the design's flag rows.
const WARMING_TOGGLES = ['reactions_enabled', 'join_enabled', 'inter_account_chat'] as const;
type WarmingToggle = (typeof WARMING_TOGGLES)[number];

// Parse a numeric field, clamping to [min, max] and falling back on empty/NaN.
// Keeps a fat-fingered value from failing the backend's Field bounds with a 422.
function clampNumber(raw: string, min: number, max: number, fallback: number): number {
  const n = Number(raw);
  if (raw.trim() === '' || Number.isNaN(n)) return fallback;
  return Math.min(max, Math.max(min, n));
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
  // Gemini rate-limit knobs (see the "?" hints): retry count + min spacing between calls.
  const [geminiRetries, setGeminiRetries] = useState(String(settings.gemini_max_retries ?? 1));
  const [geminiInterval, setGeminiInterval] = useState(
    String(settings.gemini_min_interval_seconds ?? 0),
  );
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
              gemini_model: settings.gemini_model,
              gemini_max_retries: clampNumber(geminiRetries, 0, 5, 1),
              gemini_min_interval_seconds: clampNumber(geminiInterval, 0, 60, 0),
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
    setGeminiRetries(String(settings.gemini_max_retries ?? 1));
    setGeminiInterval(String(settings.gemini_min_interval_seconds ?? 0));
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
      noValidate
      onSubmit={(event) => {
        event.preventDefault();
        void form.handleSubmit();
      }}
    >
      <Card mb="mb-lg" title={t('settings.api.title')} subtitle={t('settings.api.subtitle')}>
        <div className="space-y-lg">
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
          <div className="grid grid-cols-1 md:grid-cols-2 gap-md">
            <label className="block">
              <span className={`${FIELD_LABEL} flex items-center gap-sm`}>
                {t('settings.api.geminiRetries')}
                <HelpHint
                  text={t('settings.api.geminiRetriesHelp')}
                  example={t('settings.api.geminiRetriesExample')}
                />
              </span>
              <Input
                type="number"
                min={0}
                max={5}
                inputMode="numeric"
                value={geminiRetries}
                onChange={(event) => {
                  setGeminiRetries(event.target.value);
                }}
                aria-label={t('settings.api.geminiRetries')}
              />
            </label>
            <label className="block">
              <span className={`${FIELD_LABEL} flex items-center gap-sm`}>
                {t('settings.api.geminiInterval')}
                <HelpHint
                  text={t('settings.api.geminiIntervalHelp')}
                  example={t('settings.api.geminiIntervalExample')}
                />
              </span>
              <Input
                type="number"
                min={0}
                max={60}
                step="0.5"
                inputMode="decimal"
                value={geminiInterval}
                onChange={(event) => {
                  setGeminiInterval(event.target.value);
                }}
                aria-label={t('settings.api.geminiInterval')}
              />
            </label>
          </div>
        </div>
      </Card>

      <Card
        mb="mb-lg"
        title={t('settings.captchaLlm.title')}
        subtitle={t('settings.captchaLlm.subtitle')}
      >
        <SegmentedControl
          variant="outline"
          value={provider}
          ariaLabel={t('settings.captchaLlm.title')}
          options={(['gemini', 'openai'] as const).map((option) => ({
            value: option,
            label: t(`settings.captchaLlm.${option}`),
          }))}
          onChange={(option) => {
            setProvider(option);
          }}
        />
      </Card>

      <Card
        mb="mb-lg"
        title={t('settings.warmLimits.title')}
        subtitle={t('settings.warmLimits.subtitle')}
      >
        <div className="rounded-lg border border-dashed border-line bg-surface px-lg py-md text-body text-ink-subtle">
          {t('settings.warmLimits.engineNote')}
        </div>
      </Card>

      <Card
        mb="mb-lg"
        title={t('settings.neuroLimits.title')}
        subtitle={t('settings.neuroLimits.subtitle')}
      >
        <div className="grid grid-cols-1 md:grid-cols-2 gap-md">
          <form.Field name="cpd">
            {(field) => (
              <FormField field={field} label={t('settings.neuroLimits.cpd')} inputMode="numeric" />
            )}
          </form.Field>
          <div className="min-w-0">
            <span className={FIELD_LABEL}>{t('settings.neuroLimits.delay')}</span>
            <div className="flex items-center gap-md">
              <form.Field name="delayFrom">
                {(field) => (
                  <label className="tb-time flex min-w-0 flex-1 items-center gap-sm rounded-lg border border-line bg-white px-md py-md">
                    <span className="shrink-0 type-caption">{t('settings.range.from')}</span>
                    <input
                      inputMode="numeric"
                      value={field.state.value}
                      onChange={(event) => {
                        field.handleChange(event.target.value);
                      }}
                      onBlur={field.handleBlur}
                      aria-label={t('settings.neuroLimits.delayFrom')}
                      className="min-w-0 flex-1 border-none bg-transparent text-right text-lead outline-none"
                    />
                  </label>
                )}
              </form.Field>
              <form.Field name="delayTo">
                {(field) => (
                  <label className="tb-time flex min-w-0 flex-1 items-center gap-sm rounded-lg border border-line bg-white px-md py-md">
                    <span className="shrink-0 type-caption">{t('settings.range.to')}</span>
                    <input
                      inputMode="numeric"
                      value={field.state.value}
                      onChange={(event) => {
                        field.handleChange(event.target.value);
                      }}
                      onBlur={field.handleBlur}
                      aria-label={t('settings.neuroLimits.delayTo')}
                      className="min-w-0 flex-1 border-none bg-transparent text-right text-lead outline-none"
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

      <Card className="px-xl py-tight" mb="mb-xl">
        {WARMING_TOGGLES.map((flag) => (
          <div
            key={flag}
            className="flex items-center justify-between gap-md border-b border-line-row py-lg"
          >
            <div>
              <div className="type-card-title">{t(`settings.flag.${flag}.label`)}</div>
              <div className="mt-px type-caption">{t(`settings.flag.${flag}.desc`)}</div>
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

      <div className="flex justify-end gap-sm">
        <Button onClick={onCancel}>{t('settings.cancel')}</Button>
        <Button
          variant="primary"
          type="submit"
          disabled={pending || !canSubmit}
          className={
            justSaved
              ? 'bg-success-deep hover:bg-success-deep'
              : saveFailed
                ? 'bg-danger hover:bg-danger'
                : ''
          }
        >
          {justSaved ? (
            <span className="inline-flex items-center gap-sm">
              <span className="tb-swapin inline-flex">
                <Icon name="check" size={16} />
              </span>
              <span className="tb-swapin inline-block" style={{ animationDelay: '0.09s' }}>
                {t('settings.saved')}
              </span>
            </span>
          ) : saveFailed ? (
            <span className="inline-flex items-center gap-sm">
              <span className="tb-swapin inline-flex">
                <Icon name="close" size={16} />
              </span>
              <span className="tb-swapin inline-block" style={{ animationDelay: '0.09s' }}>
                {t('settings.saveFailed')}
              </span>
            </span>
          ) : (
            t('settings.save')
          )}
        </Button>
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
    // eslint-disable-next-line design-tokens/no-raw-values -- see the note in the rule: this page's own settings column
    <div className="tb-fadeup max-w-[760px]">
      <h1 className="m-0 mb-xl type-page-title">{t('settings.title')}</h1>
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
