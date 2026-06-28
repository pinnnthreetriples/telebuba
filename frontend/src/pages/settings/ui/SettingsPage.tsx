import { useForm } from '@tanstack/react-form';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { z } from 'zod';

import { updateWarmingSettingsMutation, warmingSettingsQueryOptions } from '@/entities/warming';
import type { WarmingSettings } from '@/shared/api';

const HOUR_MAX = 23;
const TOGGLES = [
  'reactions_enabled',
  'join_enabled',
  'inter_account_chat',
  'enforce_readiness',
  'quiet_hours_enabled',
] as const;

const schema = z.object({
  reactions_enabled: z.boolean(),
  join_enabled: z.boolean(),
  inter_account_chat: z.boolean(),
  enforce_readiness: z.boolean(),
  quiet_hours_enabled: z.boolean(),
  quiet_hours_start: z.number().int().min(0).max(HOUR_MAX),
  quiet_hours_end: z.number().int().min(0).max(HOUR_MAX),
  gemini_model: z.string().min(1),
  gemini_api_key: z.string(),
});

type FormValues = z.infer<typeof schema>;

function SettingsForm({ settings }: { settings: WarmingSettings }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const save = useMutation(updateWarmingSettingsMutation());

  const form = useForm({
    defaultValues: {
      reactions_enabled: settings.reactions_enabled ?? true,
      join_enabled: settings.join_enabled ?? true,
      inter_account_chat: settings.inter_account_chat ?? false,
      enforce_readiness: settings.enforce_readiness ?? true,
      quiet_hours_enabled: settings.quiet_hours_enabled ?? false,
      quiet_hours_start: settings.quiet_hours_start ?? 0,
      quiet_hours_end: settings.quiet_hours_end ?? 0,
      gemini_model: settings.gemini_model,
      gemini_api_key: '',
    } satisfies FormValues,
    validators: { onChange: schema },
    onSubmit: async ({ value }) => {
      await save.mutateAsync({
        body: {
          reactions_enabled: value.reactions_enabled,
          join_enabled: value.join_enabled,
          inter_account_chat: value.inter_account_chat,
          enforce_readiness: value.enforce_readiness,
          quiet_hours_enabled: value.quiet_hours_enabled,
          quiet_hours_start: value.quiet_hours_start,
          quiet_hours_end: value.quiet_hours_end,
          max_daily_actions: 0,
          gemini_model: value.gemini_model,
          gemini_api_key: value.gemini_api_key.trim() === '' ? null : value.gemini_api_key,
          clear_gemini_key: false,
        },
      });
      await queryClient.invalidateQueries();
    },
  });

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        void form.handleSubmit();
      }}
      className="space-y-5 rounded-md border border-line bg-surface p-5"
    >
      <div className="space-y-2">
        {TOGGLES.map((field) => (
          <form.Field key={field} name={field}>
            {(f) => (
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={f.state.value}
                  onChange={(event) => {
                    f.handleChange(event.target.checked);
                  }}
                />
                {t(`settings.field.${field}`)}
              </label>
            )}
          </form.Field>
        ))}
      </div>

      <div className="flex gap-4">
        <form.Field name="quiet_hours_start">
          {(f) => (
            <label className="text-sm">
              <span className="mb-1 block text-ink-muted">
                {t('settings.field.quiet_hours_start')}
              </span>
              <input
                type="number"
                min={0}
                max={HOUR_MAX}
                value={f.state.value}
                onChange={(event) => {
                  f.handleChange(event.target.valueAsNumber);
                }}
                className="w-20 rounded-md border border-line px-2 py-1"
              />
              {f.state.meta.errors.length > 0 ? (
                <span className="mt-1 block text-xs text-danger">{t('settings.hourRange')}</span>
              ) : null}
            </label>
          )}
        </form.Field>
        <form.Field name="quiet_hours_end">
          {(f) => (
            <label className="text-sm">
              <span className="mb-1 block text-ink-muted">
                {t('settings.field.quiet_hours_end')}
              </span>
              <input
                type="number"
                min={0}
                max={HOUR_MAX}
                value={f.state.value}
                onChange={(event) => {
                  f.handleChange(event.target.valueAsNumber);
                }}
                className="w-20 rounded-md border border-line px-2 py-1"
              />
              {f.state.meta.errors.length > 0 ? (
                <span className="mt-1 block text-xs text-danger">{t('settings.hourRange')}</span>
              ) : null}
            </label>
          )}
        </form.Field>
      </div>

      <form.Field name="gemini_model">
        {(f) => (
          <label className="block text-sm">
            <span className="mb-1 block text-ink-muted">{t('settings.field.gemini_model')}</span>
            <input
              value={f.state.value}
              onChange={(event) => {
                f.handleChange(event.target.value);
              }}
              className="w-full rounded-md border border-line px-3 py-2"
            />
            {f.state.meta.errors.length > 0 ? (
              <span className="mt-1 block text-xs text-danger">{t('settings.required')}</span>
            ) : null}
          </label>
        )}
      </form.Field>

      <form.Field name="gemini_api_key">
        {(f) => (
          <label className="block text-sm">
            <span className="mb-1 block text-ink-muted">{t('settings.field.gemini_api_key')}</span>
            <input
              type="password"
              value={f.state.value}
              onChange={(event) => {
                f.handleChange(event.target.value);
              }}
              placeholder={settings.has_gemini_key ? t('settings.keySet') : t('settings.keyUnset')}
              className="w-full rounded-md border border-line px-3 py-2"
            />
          </label>
        )}
      </form.Field>

      <form.Subscribe selector={(state) => [state.canSubmit, state.isDirty, state.isSubmitting]}>
        {([canSubmit, isDirty, isSubmitting]) => (
          <button
            type="submit"
            disabled={!canSubmit || !isDirty || isSubmitting}
            className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
          >
            {save.isSuccess && !isDirty ? t('settings.saved') : t('settings.save')}
          </button>
        )}
      </form.Subscribe>
    </form>
  );
}

export function SettingsPage() {
  const { t } = useTranslation();
  const { data, isPending, isError } = useQuery(warmingSettingsQueryOptions());

  return (
    <main className="mx-auto max-w-2xl space-y-4 p-8">
      <h1 className="text-2xl font-semibold">{t('settings.title')}</h1>
      {isPending ? (
        <p className="text-ink-muted">{t('settings.loading')}</p>
      ) : isError || !data ? (
        <p role="alert" className="text-danger">
          {t('settings.error')}
        </p>
      ) : (
        <SettingsForm settings={data} />
      )}
    </main>
  );
}
