import { useTranslation } from 'react-i18next';

import type { WarmingAccountState } from '@/shared/api';
import { Icon, type IconName } from '@/shared/ui';

// While a visit runs, a live card whose colour, glow and words are the step the
// account is on right now (`last_action` along `cycle_plan`, item 4.4): reading is
// blue, reacting amber, joining green, stories coral, a DM deep blue. Re-keyed on the
// step, so each change of step fades the new state in. The engine only moves
// `last_action` forward along the plan, so the bar never steps back.
type Step = NonNullable<WarmingAccountState['cycle_plan']>[number];

const STEP_ICON: Record<Step, IconName> = {
  set_online: 'globe',
  stories: 'play',
  join: 'user-plus',
  read: 'eye',
  react: 'sparkles',
  extras: 'sliders-horizontal',
  send_dm: 'send',
};

const STEP_TONE: Record<Step, string> = {
  set_online: 'calm',
  stories: 'stories',
  join: 'join',
  read: 'read',
  react: 'react',
  extras: 'calm',
  send_dm: 'dm',
};

// Steps whose `last_channel` is the channel they touch.
const CHANNEL_STEPS = new Set<Step>(['join', 'read', 'react']);

function Neighbour({ step, kind }: { step: Step | undefined; kind: 'done' | 'next' }) {
  const { t } = useTranslation();
  if (!step) return null;
  return (
    <span className="flex min-w-0 flex-1 items-center gap-1 tb-visit-sub">
      <Icon name={kind === 'done' ? 'check' : 'chevron-right'} size={12} className="shrink-0" />
      <span className="truncate">
        {kind === 'done'
          ? t('warming.card.stepDone', { step: t(`warming.card.step.${step}`) })
          : t('warming.card.stepNext', { step: t(`warming.card.step.${step}`) })}
      </span>
    </span>
  );
}

export function VisitSteps({ account }: { account: WarmingAccountState }) {
  const { t } = useTranslation();
  const plan = account.cycle_plan ?? [];
  if (account.state !== 'active' || !account.visit_kind || plan.length === 0) return null;
  const at = Math.max(0, plan.indexOf(account.last_action as Step));
  const current = plan[at] ?? plan[0];
  if (!current) return null;
  const percent = Math.round(Math.min(1, (at + 0.5) / plan.length) * 100);
  const channel = CHANNEL_STEPS.has(current) && account.last_channel ? account.last_channel : null;
  return (
    <div
      key={current}
      data-tone={STEP_TONE[current]}
      className="tb-visit tb-swapin rounded-lg p-4"
      role="status"
    >
      <div className="flex min-w-0 items-center gap-2">
        <span className="tb-glass flex size-tile shrink-0 items-center justify-center rounded-full border text-on-fill">
          <Icon name={STEP_ICON[current]} size={16} />
        </span>
        <div className="min-w-0">
          <div className="truncate type-h2 text-on-fill">{t(`warming.card.doing.${current}`)}</div>
          <div className="truncate type-small tb-visit-sub">
            {t('warming.visit.label', { kind: t(`warming.visit.kind.${account.visit_kind}`) })}
            {channel ? ` · ${channel}` : ''}
          </div>
        </div>
      </div>
      {/* Where the visit has been and where it goes next, as one quiet line. */}
      <div className="mt-3 flex items-center gap-3 type-small">
        <Neighbour step={at > 0 ? plan[at - 1] : undefined} kind="done" />
        <Neighbour step={plan[at + 1]} kind="next" />
      </div>

      <div className="tb-glass relative mt-4 flex h-control items-center overflow-hidden rounded-full border px-4">
        <div
          className="tb-glass-fill absolute inset-y-0 left-0 rounded-full"
          style={{ width: `${String(percent)}%` }}
        />
        <span className="relative type-h3 tabular-nums text-on-fill">{percent}%</span>
        <span className="relative ml-auto type-small tb-visit-sub">
          {t('warming.card.stepOf', { at: at + 1, total: plan.length })}
        </span>
      </div>
    </div>
  );
}
