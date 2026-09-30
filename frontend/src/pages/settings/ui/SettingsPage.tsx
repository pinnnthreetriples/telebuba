import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { updateWarmingSettingsMutation, warmingSettingsQueryOptions } from '@/entities/warming';
import type { WarmingSettings } from '@/shared/api';
import { Button, Card, HelpHint, Icon, Input, Notice, SegmentedControl } from '@/shared/ui';

import { ApiKeyField } from './ApiKeyField';

const FIELD_LABEL = 'mb-tight block type-label';

// The page is only what no other screen owns: the LLM keys, their pacing and the two
// provider choices. The warming toggles live on the warming board's action-tuning card
// and the neurocomment limits in the listener modal — duplicated here, each save sent
// the other page's fields back from a stale cache and could undo an edit made there.
// OpenAI solves captchas only, so its key comes last.
const PROVIDERS = ['gemini', 'deepseek', 'openai'] as const;
type Provider = (typeof PROVIDERS)[number];
const TEXT_PROVIDERS = ['gemini', 'deepseek'] as const;
type TextProvider = (typeof TEXT_PROVIDERS)[number];
// The names the key fields carry, for the "no key" warnings.
const KEY_NAME: Record<Provider, string> = {
  gemini: 'Gemini',
  deepseek: 'DeepSeek',
  openai: 'OpenAI',
};

// A typed key replaces the stored one, blank keeps it, `clear` wipes it on save.
type KeyDraft = { value: string; show: boolean; clear: boolean };
const NO_DRAFT: KeyDraft = { value: '', show: false, clear: false };
const NO_DRAFTS: Record<Provider, KeyDraft> = {
  gemini: NO_DRAFT,
  openai: NO_DRAFT,
  deepseek: NO_DRAFT,
};

// Parse a numeric field, clamping to [min, max] and falling back on empty/NaN.
// Keeps a fat-fingered value from failing the backend's Field bounds with a 422.
function clampNumber(raw: string, min: number, max: number, fallback: number): number {
  const n = Number(raw);
  if (raw.trim() === '' || Number.isNaN(n)) return fallback;
  return Math.min(max, Math.max(min, n));
}

function stored(settings: WarmingSettings, provider: Provider): { set: boolean; hint: string } {
  const set = {
    gemini: settings.has_gemini_key,
    openai: settings.has_openai_key,
    deepseek: settings.has_deepseek_key,
  }[provider];
  const hint = {
    gemini: settings.gemini_key_hint,
    openai: settings.openai_key_hint,
    deepseek: settings.deepseek_key_hint,
  }[provider];
  return { set: set ?? false, hint: hint ?? '••••' };
}

// Clear wins over a typed key, a typed key sets it, blank (`null`) keeps it.
function keyValue(draft: KeyDraft): string | null {
  const value = draft.value.trim();
  return draft.clear || value === '' ? null : value;
}

// A "no key" warning under a provider choice, as an i18n key plus its values.
type Warning = { key: string; values?: Record<string, string> } | null;

// The backend's rule (`services/_text_llm.py`): a chosen provider without a key hands
// over to the other one; with neither key there is nothing to hand over to.
function textWarning(chosen: TextProvider, hasKey: (provider: Provider) => boolean): Warning {
  if (hasKey(chosen)) return null;
  const other: TextProvider = chosen === 'gemini' ? 'deepseek' : 'gemini';
  return hasKey(other)
    ? {
        key: 'settings.textLlm.fallback',
        values: { chosen: KEY_NAME[chosen], other: KEY_NAME[other] },
      }
    : { key: 'settings.textLlm.noKey' };
}

// The solver's rule (`services/neurocomment/challenge.py`): anything without its key
// falls back to Gemini, and Gemini without one gives up.
function captchaWarning(chosen: Provider, hasKey: (provider: Provider) => boolean): Warning {
  if (hasKey(chosen)) return null;
  if (chosen === 'gemini') return { key: 'settings.captchaLlm.noKey' };
  return {
    key: hasKey('gemini') ? 'settings.captchaLlm.fallback' : 'settings.captchaLlm.noKeys',
    values: { chosen: KEY_NAME[chosen] },
  };
}

function SettingsForm({ settings }: { settings: WarmingSettings }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const saveWarm = useMutation(updateWarmingSettingsMutation());

  const [keys, setKeys] = useState(NO_DRAFTS);
  // Rate-limit knobs for every provider (see the "?" hints): retry count + min spacing
  // between one provider's calls. The fields keep their Gemini-era names.
  const [retries, setRetries] = useState(String(settings.gemini_max_retries ?? 1));
  const [pause, setPause] = useState(String(settings.gemini_min_interval_seconds ?? 0));
  const [textProvider, setTextProvider] = useState<TextProvider>(
    settings.text_llm_provider ?? 'deepseek',
  );
  const [provider, setProvider] = useState<Provider>(settings.captcha_llm_provider ?? 'gemini');
  const [justSaved, setJustSaved] = useState(false);
  const [saveFailed, setSaveFailed] = useState(false);

  const draft = (key: Provider, change: Partial<KeyDraft>) => {
    setKeys((current) => ({ ...current, [key]: { ...current[key], ...change } }));
  };

  const submit = async () => {
    setSaveFailed(false);
    try {
      await saveWarm.mutateAsync({
        // Only this page's own fields: the route keeps whatever a body leaves out.
        body: {
          gemini_api_key: keyValue(keys.gemini),
          clear_gemini_key: keys.gemini.clear,
          openai_api_key: keyValue(keys.openai),
          clear_openai_key: keys.openai.clear,
          deepseek_api_key: keyValue(keys.deepseek),
          clear_deepseek_key: keys.deepseek.clear,
          gemini_max_retries: clampNumber(retries, 0, 5, 1),
          gemini_min_interval_seconds: clampNumber(pause, 0, 60, 0),
          text_llm_provider: textProvider,
          captcha_llm_provider: provider,
        },
      });
      setKeys(NO_DRAFTS);
      setJustSaved(true);
      window.setTimeout(() => {
        setJustSaved(false);
      }, 1400);
      void queryClient.invalidateQueries({
        queryKey: warmingSettingsQueryOptions().queryKey,
      });
    } catch {
      setSaveFailed(true);
      window.setTimeout(() => {
        setSaveFailed(false);
      }, 2400);
    }
  };

  const pending = saveWarm.isPending;

  const onCancel = () => {
    setKeys(NO_DRAFTS);
    setRetries(String(settings.gemini_max_retries ?? 1));
    setPause(String(settings.gemini_min_interval_seconds ?? 0));
    setTextProvider(settings.text_llm_provider ?? 'deepseek');
    setProvider(settings.captcha_llm_provider ?? 'gemini');
  };

  // As the save would leave it: a typed key sets one, a pending clear removes it.
  const hasKey = (key: Provider) =>
    keys[key].value.trim() !== '' || (stored(settings, key).set && !keys[key].clear);
  const textNote = textWarning(textProvider, hasKey);
  const captchaNote = captchaWarning(provider, hasKey);

  const keyField = (key: Provider) => {
    const current = keys[key];
    const { set, hint } = stored(settings, key);
    // The stored key is present unless the operator just chose to clear it.
    const keySet = set && !current.clear;
    return (
      <ApiKeyField
        key={key}
        label={t(`settings.api.${key}Key`)}
        value={current.value}
        show={current.show}
        keySet={keySet}
        placeholder={
          current.clear
            ? t('settings.api.keyCleared')
            : !keySet
              ? t('settings.api.keyUnset')
              : // The eye reveals the stored key's hint — never the key, which stays server-side.
                current.show
                ? t('settings.api.keySetHint', { hint })
                : t('settings.api.keySet')
        }
        toggleLabel={t('settings.api.toggleVisibility')}
        clearLabel={t('settings.api.clearKey')}
        onChange={(value) => {
          draft(key, { value, clear: false });
        }}
        onToggleShow={() => {
          draft(key, { show: !current.show });
        }}
        onClear={() => {
          draft(key, { clear: true, value: '' });
        }}
      />
    );
  };

  return (
    <form
      noValidate
      // Зазор между карточками раздаёт форма, а не карточки: `mb` у `Card` больше нет.
      className="flex flex-col gap-lg"
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
    >
      <Card title={t('settings.api.title')} subtitle={t('settings.api.subtitle')}>
        <div className="space-y-lg">
          {PROVIDERS.map(keyField)}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-md">
            <label className="block">
              <span className={`${FIELD_LABEL} flex items-center gap-sm`}>
                {t('settings.api.retries')}
                <HelpHint
                  text={t('settings.api.retriesHelp')}
                  example={t('settings.api.retriesExample')}
                />
              </span>
              <Input
                type="number"
                min={0}
                max={5}
                inputMode="numeric"
                value={retries}
                onChange={(event) => {
                  setRetries(event.target.value);
                }}
                aria-label={t('settings.api.retries')}
              />
            </label>
            <label className="block">
              <span className={`${FIELD_LABEL} flex items-center gap-sm`}>
                {t('settings.api.interval')}
                <HelpHint
                  text={t('settings.api.intervalHelp')}
                  example={t('settings.api.intervalExample')}
                />
              </span>
              <Input
                type="number"
                min={0}
                max={60}
                step="0.5"
                inputMode="decimal"
                value={pause}
                onChange={(event) => {
                  setPause(event.target.value);
                }}
                aria-label={t('settings.api.interval')}
              />
            </label>
          </div>
        </div>
      </Card>

      <Card title={t('settings.textLlm.title')} subtitle={t('settings.textLlm.subtitle')}>
        <div className="flex flex-col gap-md">
          <SegmentedControl
            variant="outline"
            value={textProvider}
            ariaLabel={t('settings.textLlm.title')}
            options={TEXT_PROVIDERS.map((option) => ({
              value: option,
              label: t(`settings.textLlm.${option}`),
            }))}
            onChange={(option) => {
              setTextProvider(option);
            }}
          />
          {textNote && <Notice tone="warning">{t(textNote.key, textNote.values)}</Notice>}
        </div>
      </Card>

      <Card title={t('settings.captchaLlm.title')} subtitle={t('settings.captchaLlm.subtitle')}>
        <div className="flex flex-col gap-md">
          <SegmentedControl
            variant="outline"
            value={provider}
            ariaLabel={t('settings.captchaLlm.title')}
            options={PROVIDERS.map((option) => ({
              value: option,
              label: t(`settings.captchaLlm.${option}`),
            }))}
            onChange={(option) => {
              setProvider(option);
            }}
          />
          {captchaNote && <Notice tone="warning">{t(captchaNote.key, captchaNote.values)}</Notice>}
        </div>
      </Card>

      <div className="flex justify-end gap-sm">
        <Button onClick={onCancel}>{t('settings.cancel')}</Button>
        <Button
          variant="primary"
          type="submit"
          disabled={pending}
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

  return (
    // eslint-disable-next-line design-tokens/no-raw-values -- see the note in the rule: this page's own settings column
    <div className="tb-fadeup max-w-[760px]">
      <h1 className="m-0 mb-xl type-page-title">{t('settings.title')}</h1>
      {warming.isPending ? (
        <p className="text-content-muted">{t('settings.loading')}</p>
      ) : warming.isError || !warming.data ? (
        <p role="alert" className="text-danger">
          {t('settings.error')}
        </p>
      ) : (
        <SettingsForm settings={warming.data} />
      )}
    </div>
  );
}
