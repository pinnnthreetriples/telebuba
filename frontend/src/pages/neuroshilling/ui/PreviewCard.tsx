import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { NeuroshillingCampaign, NeuroshillingRole, NeuroshillingStep } from '@/shared/api';
import { CollapsibleCard } from '@/shared/ui';

import { clock, dialogueSeconds, roleTone, stepMeanSeconds } from './scenarioDraft';

// Card 3: the SAVED dialogue as it will read in the chat.
//
// Renders the server's scenario, not the form's draft — which is what makes the
// explicit save visible: what is shown here is what a run would post. Unsaved
// edits are called out rather than silently previewed.
export function PreviewCard({
  roles,
  steps,
  status,
  dirty,
  onRegenerate,
  busy,
}: {
  roles: NeuroshillingRole[];
  steps: NeuroshillingStep[];
  status: NonNullable<NeuroshillingCampaign['scenario_status']>;
  dirty: boolean;
  onRegenerate: () => void;
  busy: boolean;
}) {
  const { t } = useTranslation();
  // Bumped by "play", and part of every bubble's key: remounting the list is what
  // replays the CSS enter animation, so the whole feature is one number and a
  // staggered `animation-delay` rather than a timer per row.
  const [play, setPlay] = useState(0);

  const byPosition = new Map(steps.map((step) => [step.position, step]));
  const roleIndex = new Map(roles.map((role, index) => [role.role_id, index]));
  const total = dialogueSeconds(steps);
  let elapsed = 0;

  return (
    <CollapsibleCard
      defaultOpen
      label={t('neuroshilling.preview.title')}
      headerClassName="px-lg py-lg"
      bodyClassName="px-lg pb-lg"
      header={<span className="text-lead font-semibold">{t('neuroshilling.preview.title')}</span>}
      trailing={
        <span
          className={`shrink-0 rounded-full px-md py-xs text-tiny font-semibold ${status === 'approved' ? 'bg-success-tint text-success-deep' : 'bg-canvas text-ink-muted'}`}
        >
          {t(`neuroshilling.preview.status.${status}`)}
        </span>
      }
    >
      {dirty ? (
        <div className="mb-md rounded-lg bg-warning-tint px-md py-sm text-tiny text-warning-deep">
          {t('neuroshilling.preview.unsaved')}
        </div>
      ) : null}

      {steps.length === 0 ? (
        <div className="py-xl text-center text-body text-ink-subtle">
          {t('neuroshilling.preview.none')}
        </div>
      ) : (
        <div className="flex flex-col">
          {steps.map((step, index) => {
            elapsed += stepMeanSeconds(step);
            const at = roleIndex.get(step.role_id ?? '');
            const role = at === undefined ? undefined : roles[at];
            const tone = roleTone(at ?? 0);
            const quoted =
              step.reply_to_position === null || step.reply_to_position === undefined
                ? undefined
                : byPosition.get(step.reply_to_position);
            return (
              <div key={`${String(play)}-${step.step_id}`}>
                {index > 0 ? (
                  <div className="my-sm flex items-center gap-sm">
                    <span className="h-px flex-1 bg-line" />
                    <span className="text-micro tabular-nums text-ink-subtle">
                      {t('neuroshilling.preview.pause', {
                        min: step.delay_min_seconds ?? 60,
                        max: step.delay_max_seconds ?? 180,
                      })}
                    </span>
                    <span className="h-px flex-1 bg-line" />
                  </div>
                ) : null}
                <div
                  className="tb-fadeup flex gap-md"
                  style={{ animationDelay: `${String(index * 0.12)}s` }}
                >
                  <span
                    className={`flex h-[26px] w-[26px] shrink-0 items-center justify-center rounded-full text-tiny font-bold text-white ${tone.bg}`}
                  >
                    {(role?.name ?? '?').slice(0, 1).toUpperCase()}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="mb-xs flex items-center gap-sm">
                      <span className={`text-body font-semibold ${tone.text}`}>
                        {role?.name ?? t('neuroshilling.preview.noRole')}
                      </span>
                      <span className="text-micro tabular-nums text-ink-subtle">
                        {t('neuroshilling.preview.at', { time: clock(elapsed) })}
                      </span>
                    </div>
                    {step.kind === 'reaction' ? (
                      <span className="inline-flex items-center gap-tight rounded-full border border-line bg-white px-md py-xs text-tiny text-ink-muted">
                        <span aria-hidden="true">{step.emoji ?? '·'}</span>
                        {step.target_position === null || step.target_position === undefined
                          ? t('neuroshilling.preview.reactionLoose')
                          : t('neuroshilling.preview.reaction', { position: step.target_position })}
                      </span>
                    ) : (
                      <div className="rounded-lg rounded-tl-[3px] border border-line bg-surface px-md py-sm text-body leading-[1.5]">
                        {quoted ? (
                          <span
                            className={`mb-tight block border-l-2 pl-sm text-tiny text-ink-subtle ${tone.border}`}
                          >
                            {quoted.text}
                          </span>
                        ) : null}
                        {step.text}
                      </div>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      <div className="mt-lg flex flex-wrap items-center gap-sm">
        <span className="mr-auto text-tiny tabular-nums text-ink-subtle">
          {t('neuroshilling.preview.total', { time: clock(total) })}
        </span>
        <button
          type="button"
          disabled={steps.length === 0}
          onClick={() => {
            setPlay((value) => value + 1);
          }}
          className="rounded-full border border-line bg-white px-lg py-sm text-tiny font-semibold text-ink disabled:opacity-50"
        >
          {t('neuroshilling.preview.play')}
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={onRegenerate}
          className="rounded-full border border-line bg-white px-lg py-sm text-tiny font-semibold text-primary disabled:opacity-50"
        >
          {t('neuroshilling.preview.regenerate')}
        </button>
      </div>
    </CollapsibleCard>
  );
}
