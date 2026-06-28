import { useForm } from '@tanstack/react-form';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { type ReactNode, useState } from 'react';
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

const INPUT =
  'tb-time w-full rounded-[10px] border border-line bg-white px-3 py-[9px] text-[13px] outline-none';

// The design's pill toggle: a track + a sliding thumb (replaces the checkbox).
function Toggle({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  label: string;
}) {
  return (
    <div className="flex items-center justify-between gap-3 py-[9px]">
      <span className="text-[13px] text-ink">{label}</span>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        onClick={() => {
          onChange(!checked);
        }}
        className={`tb-sw relative h-[26px] w-[44px] shrink-0 rounded-full transition-colors ${checked ? 'bg-primary' : 'bg-line-strong'}`}
      >
        <span
          className={`tb-sw-thumb absolute top-[3px] block h-5 w-5 rounded-full bg-white shadow-[0_1px_3px_rgba(0,0,0,0.3)] transition-transform ${checked ? 'translate-x-[21px]' : 'translate-x-[3px]'}`}
        />
      </button>
    </div>
  );
}

function Card({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="rounded-2xl border border-line bg-white p-5">
      <div className="mb-3 text-[14px] font-bold">{title}</div>
      {children}
    </div>
  );
}

function SettingsForm({ settings }: { settings: WarmingSettings }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [showKey, setShowKey] = useState(false);
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
      className="space-y-4"
    >
      <Card title={t('settings.section.warming')}>
        <div className="divide-y divide-line">
          {TOGGLES.map((field) => (
            <form.Field key={field} name={field}>
              {(f) => (
                <Toggle
                  checked={f.state.value}
                  onChange={f.handleChange}
                  label={t(`settings.field.${field}`)}
                />
              )}
            </form.Field>
          ))}
        </div>

        <div className="mt-4 flex gap-4">
          {(['quiet_hours_start', 'quiet_hours_end'] as const).map((field) => (
            <form.Field key={field} name={field}>
              {(f) => (
                <label className="block">
                  <span className="mb-[6px] block text-[12px] font-medium text-[#3a3a3a]">
                    {t(`settings.field.${field}`)}
                  </span>
                  <input
                    type="number"
                    min={0}
                    max={HOUR_MAX}
                    value={f.state.value}
                    onChange={(event) => {
                      f.handleChange(event.target.valueAsNumber);
                    }}
                    className={`${INPUT} w-24`}
                  />
                  {f.state.meta.errors.length > 0 ? (
                    <span className="mt-1 block text-[11px] text-danger">
                      {t('settings.hourRange')}
                    </span>
                  ) : null}
                </label>
              )}
            </form.Field>
          ))}
        </div>
      </Card>

      <Card title={t('settings.section.api')}>
        <form.Field name="gemini_model">
          {(f) => (
            <label className="mb-4 block">
              <span className="mb-[6px] block text-[12px] font-medium text-[#3a3a3a]">
                {t('settings.field.gemini_model')}
              </span>
              <input
                value={f.state.value}
                onChange={(event) => {
                  f.handleChange(event.target.value);
                }}
                className={INPUT}
              />
              {f.state.meta.errors.length > 0 ? (
                <span className="mt-1 block text-[11px] text-danger">{t('settings.required')}</span>
              ) : null}
            </label>
          )}
        </form.Field>

        <form.Field name="gemini_api_key">
          {(f) => (
            <label className="block">
              <span className="mb-[6px] block text-[12px] font-medium text-[#3a3a3a]">
                {t('settings.field.gemini_api_key')}
              </span>
              <div className="flex gap-2">
                <input
                  type={showKey ? 'text' : 'password'}
                  value={f.state.value}
                  onChange={(event) => {
                    f.handleChange(event.target.value);
                  }}
                  placeholder={
                    settings.has_gemini_key ? t('settings.keySet') : t('settings.keyUnset')
                  }
                  className={`${INPUT} flex-1 font-mono`}
                />
                <button
                  type="button"
                  aria-label={t('settings.field.gemini_api_key')}
                  onClick={() => {
                    setShowKey((value) => !value);
                  }}
                  className="flex w-[42px] items-center justify-center rounded-[10px] border border-line bg-white text-ink-muted"
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
          )}
        </form.Field>
      </Card>

      <form.Subscribe selector={(state) => [state.canSubmit, state.isDirty, state.isSubmitting]}>
        {([canSubmit, isDirty, isSubmitting]) => (
          <button
            type="submit"
            disabled={!canSubmit || !isDirty || isSubmitting}
            className="rounded-full bg-primary px-5 py-[10px] text-[13px] font-medium text-white disabled:opacity-50"
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
    <div className="tb-fadeup mx-auto max-w-[640px]">
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
