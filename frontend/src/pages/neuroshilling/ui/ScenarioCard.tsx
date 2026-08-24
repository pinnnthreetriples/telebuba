import { useTranslation } from 'react-i18next';

import type { NeuroshillingCampaign } from '@/shared/api';
import { CollapsibleCard, HelpHint, Select, Switch } from '@/shared/ui';

import type { DraftRole, DraftStep, ScenarioDraft } from './scenarioDraft';
import {
  clampDelay,
  MAX_GENERATED_STEPS,
  MAX_ROLES,
  MAX_STEP_DELAY_SECONDS,
  MAX_STEPS,
  mintKey,
  REACTIONS,
  roleTone,
} from './scenarioDraft';

const FIELD =
  'w-full rounded-lg border border-line-input bg-white px-[11px] py-[8px] text-body outline-none focus:border-primary';
const PICK = 'rounded-md border border-line-input bg-white px-[9px] py-[6px] text-body';
const GHOST_BUTTON =
  'flex items-center justify-center gap-tight rounded-lg border border-dashed border-primary-line bg-white py-[9px] text-body font-medium text-primary hover:border-primary hover:bg-primary-wash disabled:opacity-50';

function Stepper({
  label,
  value,
  min,
  max,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  onChange: (value: number) => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex items-center gap-sm">
      <span className="text-body text-ink-muted">{label}</span>
      <div className="inline-flex items-center gap-[2px] rounded-full border border-line-input bg-white px-[4px] py-[2px]">
        <button
          type="button"
          aria-label={t('neuroshilling.scenario.stepper.less', { label })}
          disabled={value <= min}
          onClick={() => {
            onChange(value - 1);
          }}
          className="h-[20px] w-[20px] rounded-full text-lead text-ink-muted disabled:opacity-40"
        >
          −
        </button>
        <span className="min-w-[18px] text-center text-body font-semibold tabular-nums">
          {value}
        </span>
        <button
          type="button"
          aria-label={t('neuroshilling.scenario.stepper.more', { label })}
          disabled={value >= max}
          onClick={() => {
            onChange(value + 1);
          }}
          className="h-[20px] w-[20px] rounded-full text-lead text-ink-muted disabled:opacity-40"
        >
          +
        </button>
      </div>
    </div>
  );
}

function StepRow({
  step,
  index,
  earlier,
  roles,
  onChange,
  onRemove,
}: {
  step: DraftStep;
  index: number;
  earlier: number[];
  roles: DraftRole[];
  onChange: (patch: Partial<DraftStep>) => void;
  onRemove: () => void;
}) {
  const { t } = useTranslation();
  const position = index + 1;
  const link = step.kind === 'reaction' ? step.targetPosition : step.replyToPosition;
  const linkLabel =
    step.kind === 'reaction'
      ? t('neuroshilling.scenario.steps.target', { position })
      : t('neuroshilling.scenario.steps.replyTo', { position });

  return (
    <div className="rounded-lg border border-line bg-white p-[11px]">
      <div className="mb-[8px] flex items-center gap-sm">
        <span className="rounded-full bg-track px-[8px] py-[2px] text-micro font-semibold tabular-nums text-ink-muted">
          {t('neuroshilling.scenario.steps.position', { position })}
        </span>
        <div className="min-w-0 flex-1">
          <Select
            value={step.roleId ?? ''}
            onChange={(value) => {
              onChange({ roleId: value || null });
            }}
            options={[
              { value: '', label: t('neuroshilling.scenario.steps.rolePick') },
              ...roles.map((role) => ({ value: role.roleId, label: role.name })),
            ]}
            ariaLabel={t('neuroshilling.scenario.steps.role', { position })}
          />
        </div>
        <button
          type="button"
          aria-label={t('neuroshilling.scenario.steps.remove', { position })}
          onClick={onRemove}
          className="h-[24px] shrink-0 rounded-sm border border-line bg-white px-[8px] text-body text-ink-subtle hover:border-danger-line hover:bg-danger-tint hover:text-danger"
        >
          ×
        </button>
      </div>

      {step.kind === 'message' ? (
        <textarea
          rows={2}
          value={step.text}
          maxLength={1000}
          placeholder={t('neuroshilling.scenario.steps.textPlaceholder')}
          aria-label={t('neuroshilling.scenario.steps.text', { position })}
          onChange={(event) => {
            onChange({ text: event.target.value });
          }}
          className={`${FIELD} mb-[8px] resize-none font-[inherit] leading-[1.5]`}
        />
      ) : (
        <div
          role="radiogroup"
          aria-label={t('neuroshilling.scenario.steps.emoji', { position })}
          className="mb-[8px] flex flex-wrap gap-tight"
        >
          {REACTIONS.map((emoji) => (
            <button
              key={emoji}
              type="button"
              role="radio"
              aria-checked={step.emoji === emoji}
              aria-label={emoji}
              onClick={() => {
                onChange({ emoji });
              }}
              className={`h-[28px] w-[28px] rounded-md border text-lead ${step.emoji === emoji ? 'border-primary bg-primary/[0.08]' : 'border-line-input bg-white'}`}
            >
              {emoji}
            </button>
          ))}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-md">
        <div className="w-[150px]">
          <Select
            value={link === null ? '' : String(link)}
            onChange={(value) => {
              const next = value === '' ? null : Number(value);
              onChange(
                step.kind === 'reaction' ? { targetPosition: next } : { replyToPosition: next },
              );
            }}
            options={[
              {
                value: '',
                label:
                  step.kind === 'reaction'
                    ? t('neuroshilling.scenario.steps.targetNone')
                    : t('neuroshilling.scenario.steps.replyNone'),
              },
              ...earlier.map((value) => ({
                value: String(value),
                label: t('neuroshilling.scenario.steps.position', { position: value }),
              })),
            ]}
            ariaLabel={linkLabel}
          />
        </div>
        <div className="flex items-center gap-tight text-tiny text-ink-subtle">
          <span>{t('neuroshilling.scenario.steps.delay')}</span>
          <input
            type="number"
            min={0}
            max={MAX_STEP_DELAY_SECONDS}
            value={step.delayMinSeconds}
            aria-label={t('neuroshilling.scenario.steps.delayMin', { position })}
            onChange={(event) => {
              const value = clampDelay(Number(event.target.value));
              // Clamped in pairs: `delay_min > delay_max` is a 422 from the input
              // schema, which reaches the operator as an unreadable validation blob.
              onChange({
                delayMinSeconds: value,
                delayMaxSeconds: Math.max(value, step.delayMaxSeconds),
              });
            }}
            className={`${PICK} w-[62px] tabular-nums`}
          />
          <span>–</span>
          <input
            type="number"
            min={0}
            max={MAX_STEP_DELAY_SECONDS}
            value={step.delayMaxSeconds}
            aria-label={t('neuroshilling.scenario.steps.delayMax', { position })}
            onChange={(event) => {
              const value = clampDelay(Number(event.target.value));
              onChange({
                delayMaxSeconds: value,
                delayMinSeconds: Math.min(value, step.delayMinSeconds),
              });
            }}
            className={`${PICK} w-[62px] tabular-nums`}
          />
          <span>{t('neuroshilling.scenario.steps.seconds')}</span>
        </div>
      </div>
    </div>
  );
}

// Card 2: everything that decides WHAT gets said — the brief the model is given
// and the dialogue itself.
//
// Zero hooks besides `useTranslation`, like its two sibling cards: the draft and
// every request live on the page, which is also what lets the "regenerate" button
// on the preview card reach the same state this one edits.
export function ScenarioCard({
  draft,
  onDraft,
  status,
  dirty,
  personaCount,
  stepCount,
  onPersonaCount,
  onStepCount,
  onGenerate,
  onSave,
  onApprove,
  busy,
}: {
  draft: ScenarioDraft;
  onDraft: (draft: ScenarioDraft) => void;
  status: NonNullable<NeuroshillingCampaign['scenario_status']>;
  dirty: boolean;
  personaCount: number;
  stepCount: number;
  onPersonaCount: (value: number) => void;
  onStepCount: (value: number) => void;
  onGenerate: () => void;
  onSave: () => void;
  onApprove: () => void;
  busy: boolean;
}) {
  const { t } = useTranslation();
  const namelessRole = draft.roles.some((role) => !role.name.trim());

  const patchStep = (index: number, patch: Partial<DraftStep>) => {
    onDraft({
      ...draft,
      steps: draft.steps.map((step, at) => (at === index ? { ...step, ...patch } : step)),
    });
  };

  // Removing a step renumbers everything after it, so every one-based reference
  // into the list moves with it: a link AT the removed step is dropped, a link
  // past it slides down one. Left alone these become the forward/dangling links
  // the server refuses on save.
  const removeStep = (index: number) => {
    const shift = (value: number | null) => {
      if (value === null || value === index + 1) return null;
      return value > index + 1 ? value - 1 : value;
    };
    onDraft({
      ...draft,
      mediaStepPosition: shift(draft.mediaStepPosition),
      steps: draft.steps
        .filter((_, at) => at !== index)
        .map((step) => ({
          ...step,
          replyToPosition: shift(step.replyToPosition),
          targetPosition: shift(step.targetPosition),
        })),
    });
  };

  const addStep = (kind: DraftStep['kind']) => {
    onDraft({
      ...draft,
      steps: [
        ...draft.steps,
        {
          key: mintKey('step'),
          kind,
          roleId: draft.roles[0]?.roleId ?? null,
          text: '',
          replyToPosition: null,
          targetPosition: null,
          emoji: kind === 'reaction' ? REACTIONS[0]! : null,
          delayMinSeconds: 60,
          delayMaxSeconds: 180,
        },
      ],
    });
  };

  return (
    <CollapsibleCard
      defaultOpen
      label={t('neuroshilling.scenario.title')}
      headerClassName="px-4 py-[15px]"
      bodyClassName="px-4 pb-[15px]"
      header={<span className="text-lead font-semibold">{t('neuroshilling.scenario.title')}</span>}
      trailing={
        // The approval dies on THIS card, so it has to be visible on THIS card.
        // Every edit below returns the campaign to `draft` the moment it is saved,
        // and an operator who only ever saw the badge on the preview would find
        // that out from a refused launch.
        <span
          className={`shrink-0 rounded-full px-[10px] py-[3px] text-tiny font-semibold ${
            dirty && status === 'approved'
              ? 'bg-warning-tint text-warning'
              : status === 'approved'
                ? 'bg-success-tint text-success'
                : 'bg-track text-ink-muted'
          }`}
        >
          {dirty && status === 'approved'
            ? t('neuroshilling.scenario.status.willReset')
            : t(`neuroshilling.scenario.status.${status}`)}
        </span>
      }
    >
      <div className="mb-[12px] flex items-center gap-sm">
        <div
          role="radiogroup"
          aria-label={t('neuroshilling.scenario.mode.label')}
          className="inline-flex rounded-full border border-line-input bg-white p-[3px]"
        >
          {(['campaign', 'revive'] as const).map((mode) => (
            <button
              key={mode}
              type="button"
              role="radio"
              aria-checked={draft.mode === mode}
              onClick={() => {
                onDraft({ ...draft, mode });
              }}
              className={`rounded-full px-[14px] py-[6px] text-body font-medium ${draft.mode === mode ? 'bg-primary text-white' : 'text-ink-muted'}`}
            >
              {t(`neuroshilling.scenario.mode.${mode}`)}
            </button>
          ))}
        </div>
        <HelpHint text={t('neuroshilling.scenario.mode.hint')} />
      </div>

      {/* A <span>, not a <label>: the control carries its own `aria-label`, and a
          second label element for the same field only makes the accessible name
          ambiguous. */}
      <span className="mb-[5px] flex items-center gap-sm text-body font-medium text-ink-muted">
        {t('neuroshilling.scenario.topic.label')}
        <HelpHint text={t('neuroshilling.scenario.topic.hint')} />
      </span>
      <textarea
        rows={3}
        value={draft.topic}
        maxLength={2000}
        placeholder={t('neuroshilling.scenario.topic.placeholder')}
        aria-label={t('neuroshilling.scenario.topic.label')}
        onChange={(event) => {
          onDraft({ ...draft, topic: event.target.value });
        }}
        className={`${FIELD} mb-[12px] resize-none font-[inherit] leading-[1.5]`}
      />

      <div className="mb-[12px] flex flex-wrap items-center gap-lg rounded-lg border border-line bg-surface p-[11px]">
        <Stepper
          label={t('neuroshilling.scenario.generate.personas')}
          value={personaCount}
          min={2}
          max={MAX_ROLES}
          onChange={onPersonaCount}
        />
        <Stepper
          label={t('neuroshilling.scenario.generate.steps')}
          value={stepCount}
          min={2}
          max={MAX_GENERATED_STEPS}
          onChange={onStepCount}
        />
        <button
          type="button"
          disabled={busy || !draft.topic.trim()}
          onClick={onGenerate}
          className="rounded-full bg-primary px-[15px] py-[7px] text-tiny font-semibold text-white disabled:opacity-50"
        >
          {t('neuroshilling.scenario.generate.run')}
        </button>
        <HelpHint text={t('neuroshilling.scenario.generate.hint')} />
      </div>

      <div className="mb-[12px] flex flex-col gap-md">
        {(
          [
            ['uniqueMessages', 'unique'],
            ['useChatContext', 'context'],
          ] as const
        ).map(([field, key]) => (
          <div key={key} className="flex items-center gap-sm">
            <Switch
              checked={draft[field]}
              label={t(`neuroshilling.scenario.${key}.label`)}
              onChange={(value) => {
                onDraft({ ...draft, [field]: value });
              }}
            />
            <span className="text-body">{t(`neuroshilling.scenario.${key}.label`)}</span>
            <HelpHint text={t(`neuroshilling.scenario.${key}.hint`)} />
          </div>
        ))}
      </div>

      <span className="mb-[5px] flex items-center gap-sm text-body font-medium text-ink-muted">
        {t('neuroshilling.scenario.media.label')}
        <HelpHint text={t('neuroshilling.scenario.media.hint')} />
      </span>
      <div className="mb-[14px] flex flex-wrap items-center gap-sm">
        <input
          value={draft.mediaMessageLink}
          maxLength={500}
          placeholder={t('neuroshilling.scenario.media.placeholder')}
          aria-label={t('neuroshilling.scenario.media.label')}
          onChange={(event) => {
            onDraft({ ...draft, mediaMessageLink: event.target.value });
          }}
          className={`${FIELD} min-w-[220px] flex-1`}
        />
        <div className="w-[150px]">
          <Select
            value={draft.mediaStepPosition === null ? '' : String(draft.mediaStepPosition)}
            onChange={(value) => {
              onDraft({ ...draft, mediaStepPosition: value === '' ? null : Number(value) });
            }}
            options={[
              { value: '', label: t('neuroshilling.scenario.media.stepNone') },
              // Messages only: the media rides along with the step's own send, and a
              // reaction sends nothing to carry it. Approval refuses such a slot with
              // `media_step_not_message`, read off the KIND of the step at that position
              // and never off its identity — which is why a generation drops the slot
              // rather than leaving it over a line the operator has never read.
              ...draft.steps.flatMap((step, index) =>
                step.kind === 'message'
                  ? [
                      {
                        value: String(index + 1),
                        label: t('neuroshilling.scenario.steps.position', {
                          position: index + 1,
                        }),
                      },
                    ]
                  : [],
              ),
            ]}
            ariaLabel={t('neuroshilling.scenario.media.step')}
          />
        </div>
      </div>

      <div className="mb-[7px] flex items-center gap-sm text-body font-semibold">
        {t('neuroshilling.scenario.roles.title')}
        <HelpHint text={t('neuroshilling.scenario.roles.hint')} />
      </div>
      <div className="mb-[9px] flex flex-col gap-sm">
        {draft.roles.map((role, index) => (
          <div key={role.roleId} className="flex items-center gap-sm">
            <span className={`h-[9px] w-[9px] shrink-0 rounded-full ${roleTone(index).bg}`} />
            <input
              value={role.name}
              maxLength={60}
              placeholder={t('neuroshilling.scenario.roles.namePlaceholder')}
              aria-label={t('neuroshilling.scenario.roles.name', { position: index + 1 })}
              onChange={(event) => {
                onDraft({
                  ...draft,
                  roles: draft.roles.map((item, at) =>
                    at === index ? { ...item, name: event.target.value } : item,
                  ),
                });
              }}
              className={`${FIELD} w-[140px] shrink-0`}
            />
            <input
              value={role.description}
              maxLength={1000}
              placeholder={t('neuroshilling.scenario.roles.descriptionPlaceholder')}
              aria-label={t('neuroshilling.scenario.roles.description', { position: index + 1 })}
              onChange={(event) => {
                onDraft({
                  ...draft,
                  roles: draft.roles.map((item, at) =>
                    at === index ? { ...item, description: event.target.value } : item,
                  ),
                });
              }}
              className={`${FIELD} min-w-0 flex-1`}
            />
            <button
              type="button"
              aria-label={t('neuroshilling.scenario.roles.remove', { position: index + 1 })}
              onClick={() => {
                // Steps keep pointing at a role that no longer exists otherwise,
                // and the server would answer 400 rather than the operator seeing
                // the empty "pick a role" select the removal really means.
                onDraft({
                  ...draft,
                  roles: draft.roles.filter((_, at) => at !== index),
                  steps: draft.steps.map((step) =>
                    step.roleId === role.roleId ? { ...step, roleId: null } : step,
                  ),
                });
              }}
              className="h-[30px] shrink-0 rounded-md border border-line bg-white px-[9px] text-body text-ink-subtle hover:border-danger-line hover:bg-danger-tint hover:text-danger"
            >
              ×
            </button>
          </div>
        ))}
        {draft.roles.length === 0 ? (
          <div className="text-body text-ink-subtle">{t('neuroshilling.scenario.roles.none')}</div>
        ) : null}
      </div>
      <button
        type="button"
        disabled={draft.roles.length >= MAX_ROLES}
        onClick={() => {
          onDraft({
            ...draft,
            roles: [...draft.roles, { roleId: mintKey('role'), name: '', description: '' }],
          });
        }}
        className={`${GHOST_BUTTON} mb-[14px] w-full`}
      >
        {t('neuroshilling.scenario.roles.add')}
      </button>

      <div className="mb-[7px] flex items-center gap-sm text-body font-semibold">
        {t('neuroshilling.scenario.steps.title')}
        <HelpHint text={t('neuroshilling.scenario.steps.hint')} />
      </div>
      <div className="mb-[9px] flex flex-col gap-sm">
        {draft.steps.map((step, index) => (
          <StepRow
            key={step.key}
            step={step}
            index={index}
            roles={draft.roles}
            // Only the messages above it: a reply or a reaction needs its target
            // to be in the chat already, and a reaction is not a message.
            earlier={draft.steps
              .slice(0, index)
              .flatMap((item, at) => (item.kind === 'message' ? [at + 1] : []))}
            onChange={(patch) => {
              patchStep(index, patch);
            }}
            onRemove={() => {
              removeStep(index);
            }}
          />
        ))}
        {draft.steps.length === 0 ? (
          <div className="text-body text-ink-subtle">{t('neuroshilling.scenario.steps.none')}</div>
        ) : null}
      </div>
      <div className="mb-[14px] flex gap-sm">
        {(['message', 'reaction'] as const).map((kind) => (
          <button
            key={kind}
            type="button"
            disabled={draft.steps.length >= MAX_STEPS}
            onClick={() => {
              addStep(kind);
            }}
            className={`${GHOST_BUTTON} flex-1`}
          >
            {t(`neuroshilling.scenario.steps.add.${kind}`)}
          </button>
        ))}
      </div>

      <div className="flex flex-wrap items-center justify-end gap-sm">
        {namelessRole ? (
          <span className="mr-auto text-tiny text-danger">
            {t('neuroshilling.scenario.roles.nameRequired')}
          </span>
        ) : null}
        <button
          type="button"
          disabled={busy || !dirty || namelessRole}
          onClick={onSave}
          className="rounded-full bg-primary px-[18px] py-[7px] text-body font-semibold text-white disabled:opacity-50"
        >
          {t('neuroshilling.scenario.save')}
        </button>
        <button
          type="button"
          disabled={busy || dirty || status === 'approved' || draft.steps.length === 0}
          title={dirty ? t('neuroshilling.scenario.approveHint') : undefined}
          onClick={onApprove}
          className="rounded-full border border-line-input bg-white px-[18px] py-[7px] text-body font-semibold text-ink disabled:opacity-50"
        >
          {t('neuroshilling.scenario.approve')}
        </button>
      </div>
    </CollapsibleCard>
  );
}
