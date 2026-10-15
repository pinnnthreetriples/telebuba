import { type ColumnDef } from '@tanstack/react-table';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  updateWarmingTuningMutation,
  warmingBoardQueryOptions,
  warmingTuningQueryOptions,
} from '@/entities/warming';
import type { WarmingTuningUpdate } from '@/shared/api';
import { mutationErrorText } from '@/shared/lib';
import {
  Button,
  CollapsibleCard,
  DataTable,
  type DataTableColumnMeta,
  FeedbackMark,
  Icon,
  Input,
  Notice,
  SegmentedControl,
} from '@/shared/ui';

import {
  CAP_MAX,
  CAP_MIN,
  capsValid,
  draftCaps,
  PERSONAS,
  type PaceRow,
  paceRows,
  PRESETS,
  type TuningPreset,
} from '../lib/pace';

type Caps = ReturnType<typeof draftCaps>;
// A table row while «Свой» is open: the limit being typed rides the row, so the column
// definitions stay put and the field keeps its focus from one keystroke to the next.
type Row = PaceRow & {
  draft?: number;
  // A save in flight freezes the draft it sent, so a later keystroke cannot be lost.
  locked: boolean;
  onEdit: (phase: PaceRow['phase'], value: number) => void;
};

// «Темп прогрева» (Прогрев 2.0, 5.2): the pace of the whole fleet is a click, not an
// `.env` edit. «Осторожно», «Обычно» and «Быстро» apply at once; «Свой» opens the limits
// for editing and applies on «Сохранить». The table under the buttons is the server's
// own cadence math, so the visits it shows are the visits the engine plans.
export function PaceCard() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const reply = useQuery(warmingTuningQueryOptions()).data;
  // The table is the cadence; a reply without it (an older backend mid-deploy) keeps the
  // card in its loading state instead of taking the warming page down with it.
  const tuning = reply?.cadence ? reply : undefined;
  const save = useMutation(updateWarmingTuningMutation());
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Caps | null>(null);

  // Seed the «Свой» draft once the real row arrives, as the action card seeds its toggles.
  const seeded = useRef(false);
  useEffect(() => {
    if (!tuning || seeded.current) return;
    seeded.current = true;
    setDraft(draftCaps(tuning));
  }, [tuning]);

  const apply = (body: WarmingTuningUpdate) => {
    save.mutate(
      { body },
      {
        onSuccess: (next) => {
          queryClient.setQueryData(warmingTuningQueryOptions().queryKey, next);
          setEditing(false);
        },
        onSettled: () => {
          // The board carries the start dialog's cadence hint, which follows the pace.
          void queryClient.invalidateQueries({ queryKey: warmingBoardQueryOptions().queryKey });
        },
      },
    );
  };

  // A finished save's mark or error belongs to what it sent, not to a draft edited since.
  const clearFeedback = () => {
    if (!save.isPending) save.reset();
  };
  const edit = (phase: PaceRow['phase'], value: number) => {
    clearFeedback();
    setDraft((prev) => (prev ? { ...prev, [phase]: value } : prev));
  };

  const pick = (preset: TuningPreset) => {
    if (preset === 'custom') {
      clearFeedback();
      if (tuning) setDraft(draftCaps(tuning));
      setEditing(true);
      return;
    }
    apply({ preset });
  };

  const rows: Row[] = tuning
    ? paceRows(tuning).map((row) => ({
        ...row,
        draft: editing ? draft?.[row.phase] : undefined,
        locked: save.isPending,
        onEdit: edit,
      }))
    : [];
  const columns = useMemo<ColumnDef<Row>[]>(
    () => [
      {
        id: 'phase',
        header: () => t('warming.pace.col.phase'),
        cell: ({ row }) => t(`warming.console.phase.${row.original.phase}`),
        meta: { cardSlot: 'title' } satisfies DataTableColumnMeta,
      },
      {
        id: 'cap',
        header: () => t('warming.pace.col.limit'),
        cell: ({ row }) => {
          const { phase, draft: typed, locked, onEdit } = row.original;
          if (typed === undefined) return row.original.cap;
          return (
            <Input
              size="sm"
              type="number"
              min={CAP_MIN}
              max={CAP_MAX}
              value={typed}
              disabled={locked}
              aria-label={t('warming.pace.limitLabel', {
                phase: t(`warming.console.phase.${phase}`),
              })}
              onChange={(event) => {
                onEdit(phase, Number(event.target.value));
              }}
            />
          );
        },
        meta: { className: 'w-col' } satisfies DataTableColumnMeta,
      },
      ...PERSONAS.map((persona): ColumnDef<Row> => ({
        id: persona,
        header: () => t(`warming.persona.${persona}.name`),
        cell: ({ row }) => row.original.visits[persona],
        meta: { cellClassName: 'text-content-secondary' } satisfies DataTableColumnMeta,
      })),
    ],
    [t],
  );

  const draftOk = draft !== null && capsValid(draft);
  const selected = editing ? 'custom' : (tuning?.preset ?? 'normal');

  return (
    <CollapsibleCard
      wrapperClassName="rounded-lg border border-line bg-surface-card"
      headerClassName="px-4 py-4"
      bodyClassName="px-4 pb-4"
      label={t('warming.pace.title')}
      icon={<Icon name="chart" size={16} />}
      title={t('warming.pace.title')}
      subtitle={t('warming.pace.subtitle')}
    >
      <SegmentedControl
        variant="outline"
        ariaLabel={t('warming.pace.title')}
        value={selected}
        disabled={!tuning || save.isPending}
        onChange={pick}
        options={PRESETS.map((preset) => ({
          value: preset,
          label: t(`warming.pace.preset.${preset}`),
          title: t(`warming.pace.hint.${preset}`),
        }))}
      />
      <p className="mt-2 type-small">{t(`warming.pace.hint.${selected}`)}</p>

      {tuning ? (
        <>
          <div className="mt-4 type-small-medium">{t('warming.pace.visitsCaption')}</div>
          <div className="mt-2 overflow-hidden rounded-md border border-line">
            <DataTable data={rows} columns={columns} getRowId={(row) => row.phase} />
          </div>
          <p className="mt-2 type-small">
            {t('warming.pace.factors', { pause: tuning.pause_factor, dm: tuning.dm_factor })}
          </p>
          {tuning.warnings?.length ? (
            <div className="mt-3 flex flex-col gap-2">
              {tuning.warnings.map((warning) => (
                <Notice key={warning.phase} tone="warning">
                  {t('warming.pace.lowCap', {
                    phase: t(`warming.console.phase.${warning.phase}`),
                    cap: warning.daily_cap,
                  })}
                </Notice>
              ))}
            </div>
          ) : null}
        </>
      ) : null}

      {editing ? (
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <Button
            variant="primary"
            size="sm"
            disabled={!draftOk || save.isPending}
            onClick={() => {
              if (!draft) return;
              apply({
                preset: 'custom',
                custom: {
                  daily_caps: draft,
                  pause_factor: tuning?.custom?.pause_factor ?? 1,
                  dm_factor: tuning?.custom?.dm_factor ?? 1,
                },
              });
            }}
          >
            {t('warming.pace.save')}
          </Button>
          <Button
            size="sm"
            onClick={() => {
              setEditing(false);
            }}
          >
            {t('warming.pace.cancel')}
          </Button>
          {draftOk ? null : (
            <span className="type-small text-danger-deep">{t('warming.pace.invalid')}</span>
          )}
        </div>
      ) : null}

      {save.isError ? (
        <div role="alert" className="mt-3 type-small text-danger-deep">
          {mutationErrorText(save.error)}
        </div>
      ) : null}
      <FeedbackMark result={save.isSuccess ? 'ok' : undefined} />
    </CollapsibleCard>
  );
}
