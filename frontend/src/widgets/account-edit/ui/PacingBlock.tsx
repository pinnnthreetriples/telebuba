import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { type ReactNode, useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  accountDisplayName,
  allAccountsQueryOptions,
  createOutreachPresetMutation,
  deleteOutreachPresetMutation,
  outreachPresetsQueryKey,
  outreachPresetsQueryOptions,
  updateOutreachPresetMutation,
} from '@/entities/account';
import type { OutreachDailyUsage, OutreachPreset } from '@/shared/api';
import {
  Button,
  CollapsibleCard,
  Icon,
  IconButton,
  Input,
  SegmentedControl,
  Slider,
} from '@/shared/ui';

import {
  BUILT_IN_PRESETS,
  type PacingKey,
  type PacingValues,
  pauseRange,
  samePacing,
  SLIDERS,
  worstZone,
  type Zone,
  zoneOf,
} from '../model/pacing';

const HINT: Record<Zone, string> = {
  ok: 'text-success-deep',
  warn: 'text-warning-deep',
  risk: 'text-danger-deep',
};

// The forecast box tints with the worst slider, so a risky setting is visible at a glance.
const ZONE_SURFACE: Record<Zone, string> = {
  ok: 'bg-canvas',
  warn: 'bg-warning-tint',
  risk: 'bg-danger-tint',
};

function presetValues(preset: OutreachPreset): PacingValues {
  return {
    pause: preset.pause_seconds,
    messages: preset.first_messages_per_day,
    contacts: preset.contact_imports_per_day,
    batch: preset.lookup_batch_size,
  };
}

function errorMessage(error: unknown): string | null {
  if (typeof error !== 'object' || error === null || !('error' in error)) return null;
  const detail = error.error;
  if (typeof detail !== 'object' || detail === null || !('message' in detail)) return null;
  return typeof detail.message === 'string' ? detail.message : null;
}

function useValueText() {
  const { t, i18n } = useTranslation();
  const number = new Intl.NumberFormat(i18n.language, { maximumFractionDigits: 1 });
  return (key: PacingKey, value: number) => {
    if (key !== 'pause') return t(`accounts.messages.pacing.value.${key}`, { value });
    const [low, high] = pauseRange(value);
    return high < 120
      ? t('accounts.messages.pacing.value.pauseSeconds', { low, high })
      : t('accounts.messages.pacing.value.pauseMinutes', {
          low: number.format(low / 60),
          high: number.format(high / 60),
        });
  };
}

function Presets({
  values,
  onChange,
}: {
  values: PacingValues;
  onChange: (values: PacingValues) => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const saved = useQuery(outreachPresetsQueryOptions()).data?.items ?? [];
  const create = useMutation(createOutreachPresetMutation());
  const update = useMutation(updateOutreachPresetMutation());
  const remove = useMutation(deleteOutreachPresetMutation());
  // null: no name field open; '' + id: renaming that preset; '' alone: saving a new one.
  const [naming, setNaming] = useState<{ id: string | null; name: string } | null>(null);

  const builtIn = BUILT_IN_PRESETS.find((preset) => samePacing(preset.values, values));
  const mine = saved.find((preset) => samePacing(presetValues(preset), values));
  const selected = builtIn?.id ?? mine?.id ?? '';
  const refresh = () => queryClient.invalidateQueries({ queryKey: outreachPresetsQueryKey() });

  const onPick = (id: string) => {
    const preset = BUILT_IN_PRESETS.find((item) => item.id === id);
    if (preset) {
      onChange(preset.values);
      return;
    }
    const own = saved.find((item) => item.id === id);
    if (own) onChange(presetValues(own));
  };

  const onSave = () => {
    if (naming === null || naming.name.trim() === '') return;
    const body = {
      name: naming.name.trim(),
      pause_seconds: values.pause,
      first_messages_per_day: values.messages,
      contact_imports_per_day: values.contacts,
      lookup_batch_size: values.batch,
    };
    const request =
      naming.id === null
        ? create.mutateAsync({ body })
        : update.mutateAsync({ path: { preset_id: naming.id }, body });
    void request
      .then(() => {
        setNaming(null);
        return refresh();
      })
      .catch(() => undefined);
  };

  const saveError = errorMessage(create.error ?? update.error);

  return (
    <section className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <SegmentedControl
          variant="outline"
          className="flex-wrap"
          ariaLabel={t('accounts.messages.pacing.mode')}
          value={selected}
          onChange={onPick}
          options={[
            ...BUILT_IN_PRESETS.map((preset) => ({
              value: preset.id,
              label: t(`accounts.messages.pacing.preset.${preset.id}`),
            })),
            ...saved.map((preset) => ({ value: preset.id, label: preset.name })),
          ]}
        />
        {selected === '' && (
          <span className="type-small">{t('accounts.messages.pacing.manual')}</span>
        )}
        {mine && (
          <>
            <IconButton
              size="sm"
              aria-label={t('accounts.messages.pacing.rename', { name: mine.name })}
              onClick={() => {
                setNaming({ id: mine.id, name: mine.name });
              }}
            >
              <Icon name="pencil" size={16} />
            </IconButton>
            <IconButton
              size="sm"
              aria-label={t('accounts.messages.pacing.delete', { name: mine.name })}
              onClick={() => {
                void remove
                  .mutateAsync({ path: { preset_id: mine.id } })
                  .then(refresh)
                  .catch(() => undefined);
              }}
            >
              <Icon name="trash" size={16} />
            </IconButton>
          </>
        )}
        {naming === null && (
          <Button
            size="sm"
            onClick={() => {
              setNaming({ id: null, name: '' });
            }}
          >
            {t('accounts.messages.pacing.save')}
          </Button>
        )}
      </div>
      {naming !== null && (
        <div className="flex flex-wrap items-center gap-2">
          <Input
            size="sm"
            maxLength={40}
            aria-label={t('accounts.messages.pacing.presetName')}
            placeholder={t('accounts.messages.pacing.presetName')}
            value={naming.name}
            onChange={(event) => {
              setNaming({ ...naming, name: event.target.value });
            }}
          />
          <Button
            size="sm"
            variant="primary"
            disabled={naming.name.trim() === ''}
            loading={create.isPending || update.isPending}
            onClick={onSave}
          >
            {t('accounts.messages.pacing.saveConfirm')}
          </Button>
          <Button
            size="sm"
            onClick={() => {
              setNaming(null);
              create.reset();
              update.reset();
            }}
          >
            {t('accounts.profile.cancel')}
          </Button>
        </div>
      )}
      {saveError !== null && (
        <p role="alert" className="type-small text-danger-deep">
          {t(
            saveError === 'preset_name_taken'
              ? 'accounts.messages.pacing.nameTaken'
              : saveError === 'too_many_presets'
                ? 'accounts.messages.pacing.tooMany'
                : 'accounts.messages.pacing.saveError',
          )}
        </p>
      )}
    </section>
  );
}

// The "pace and limits" block: presets, one slider per value with a zone-coloured hint,
// how much each selected account has left today, the caller's forecast, and an
// "Advanced" section for anything finer.
export function PacingBlock({
  keys,
  values,
  onChange,
  presets = false,
  accountIds,
  usage,
  leftKind,
  forecast,
  advanced,
}: {
  keys: PacingKey[];
  values: PacingValues;
  onChange: (values: PacingValues) => void;
  presets?: boolean;
  accountIds: string[];
  usage: Map<string, OutreachDailyUsage>;
  // Which daily cap the "left today" chips count down.
  leftKind: 'messages' | 'contacts';
  forecast?: ReactNode;
  advanced?: ReactNode;
}) {
  const { t } = useTranslation();
  const valueText = useValueText();
  const fleet = useQuery(allAccountsQueryOptions());
  const byId = new Map((fleet.data?.items ?? []).map((row) => [row.account_id, row]));
  const zone = worstZone(keys.map((key) => zoneOf(key, values[key])));
  const cap = values[leftKind];

  return (
    <div className="space-y-4">
      {presets && <Presets values={values} onChange={onChange} />}
      {keys.map((key) => {
        const slider = SLIDERS[key];
        const sliderZone = zoneOf(key, values[key]);
        return (
          <section key={key} className="space-y-1">
            <div className="flex items-baseline justify-between gap-3">
              <span className="type-body-medium text-content-secondary">
                {t(`accounts.messages.pacing.label.${key}`)}
              </span>
              <span className="type-body-medium tabular-nums">{valueText(key, values[key])}</span>
            </div>
            <Slider
              value={values[key]}
              min={slider.min}
              max={slider.max}
              step={slider.step}
              label={t(`accounts.messages.pacing.label.${key}`)}
              valueText={valueText(key, values[key])}
              onChange={(value) => {
                onChange({ ...values, [key]: value });
              }}
            />
            <p className={`type-small transition-colors duration-state ${HINT[sliderZone]}`}>
              {t(`accounts.messages.pacing.zone.${sliderZone}`)} ·{' '}
              {t(`accounts.messages.pacing.recommend.${key}`)}
            </p>
          </section>
        );
      })}
      {accountIds.length > 0 && (
        <section className="space-y-1">
          <h4 className="type-small text-content-secondary">
            {t(`accounts.messages.pacing.left.${leftKind}`)}
          </h4>
          <ul className="flex flex-wrap gap-2">
            {accountIds.map((id) => {
              const used = usage.get(id);
              const spent = leftKind === 'messages' ? used?.first_messages : used?.contact_imports;
              const left = Math.max(cap - (spent ?? 0), 0);
              return (
                <li
                  key={id}
                  className={`rounded-full px-3 py-1 type-small tabular-nums ${left === 0 ? 'bg-danger-tint text-danger-deep' : 'bg-canvas'}`}
                >
                  {t('accounts.messages.pacing.leftChip', {
                    name: accountDisplayName(byId.get(id) ?? { account_id: id }),
                    left,
                    cap,
                  })}
                </li>
              );
            })}
          </ul>
        </section>
      )}
      {forecast && (
        <div
          role="status"
          className={`rounded-md px-3 py-2 type-small transition-colors duration-state ${ZONE_SURFACE[zone]}`}
        >
          {forecast}
        </div>
      )}
      {advanced && (
        <CollapsibleCard
          label={t('accounts.messages.pacing.advanced')}
          title={t('accounts.messages.pacing.advanced')}
          wrapperClassName="rounded-md border border-line"
          headerClassName="px-3 py-2"
          bodyClassName="px-3 pb-3"
        >
          {advanced}
        </CollapsibleCard>
      )}
    </div>
  );
}
