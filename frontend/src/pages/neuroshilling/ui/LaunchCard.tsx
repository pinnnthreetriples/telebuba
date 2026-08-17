import type { TFunction } from 'i18next';
import { useTranslation } from 'react-i18next';

import type {
  LogEntry,
  NeuroshillingBoardAccount,
  NeuroshillingCampaign,
  NeuroshillingRole,
  NeuroshillingRunStatus,
  NeuroshillingStep,
} from '@/shared/api';
import { CollapsibleCard } from '@/shared/ui';
import { LogTerminal } from '@/widgets/log-terminal';

import { clock, dialogueSeconds } from './scenarioDraft';

// The minimum a dialogue needs: one account per voice, and a monologue is not a
// dialogue. It is the shipped `NEUROSHILLING__MIN_ACCOUNTS` default and it is NOT
// on the wire — the board carries no policy numbers — so the 409 stays the
// authority and this only decides what the operator is told BEFORE clicking. The
// accounts card's hint states the same two.
const MIN_ACCOUNTS = 2;

const STATUS_COLOR: Record<NonNullable<NeuroshillingRunStatus['status']>, string> = {
  idle: '#74726e',
  running: '#12a150',
  stopping: '#c47d12',
  done: '#0066ff',
  failed: '#c0473f',
};

/** Why Start is refused, in the SERVER's order, or an empty list.
 *
 * Mirrors `services.neuroshilling._runtime.start_campaign` check for check so the
 * operator reads the reason instead of collecting a 409 whose body is a bare
 * code. Where the two can disagree the server wins: this is a courtesy, not a
 * gate — which is why it is allowed to be approximate about `MIN_ACCOUNTS` and
 * exact about everything the board actually carries.
 */
function launchBlockers(
  t: TFunction,
  campaign: NeuroshillingCampaign,
  roster: NeuroshillingBoardAccount[],
  targets: string[],
  roles: NeuroshillingRole[],
  steps: NeuroshillingStep[],
): string[] {
  const reasons: string[] = [];
  if (campaign.run_mode === 'parallel') reasons.push(t('neuroshilling.launch.blocked.parallel'));
  if ((campaign.scenario_status ?? 'draft') !== 'approved') {
    reasons.push(t('neuroshilling.launch.blocked.notApproved'));
  }
  if (targets.length === 0) reasons.push(t('neuroshilling.launch.blocked.noTargets'));

  // The server counts the accounts that will PLAY: active, and not held back as
  // the substitution pool.
  const playing = roster.filter(
    (account) => (account.state ?? 'active') === 'active' && account.is_reserve !== true,
  );
  if (playing.length < MIN_ACCOUNTS) {
    reasons.push(t('neuroshilling.launch.blocked.fewAccounts', { n: playing.length }));
  } else {
    const staffed = new Set(
      playing.map((account) => account.role_id).filter((id): id is string => id != null),
    );
    // A step with no role at all refuses too — `None not in staffed` on the server.
    const orphan = steps.find((step) => step.role_id == null || !staffed.has(step.role_id));
    if (orphan !== undefined) {
      const role = roles.find((item) => item.role_id === orphan.role_id);
      reasons.push(
        role === undefined
          ? t('neuroshilling.launch.blocked.stepWithoutRole', { position: orphan.position })
          : t('neuroshilling.launch.blocked.roleWithoutAccount', { name: role.name }),
      );
    }
  }

  // `busy_owner` on a rostered account always means "held ELSEWHERE": the board
  // excludes this campaign's own claim from the map it builds.
  for (const account of roster) {
    const owner = account.busy_owner;
    if (owner == null) continue;
    reasons.push(
      t('neuroshilling.launch.blocked.accountBusy', {
        title: account.title,
        owner: t(`neuroshilling.modal.accounts.busy.${owner}`),
      }),
    );
  }
  return reasons;
}

function Tile({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-[11px] border border-line bg-[#faf9f7] px-[10px] py-[9px] text-center">
      <div className="text-[15px] font-bold tabular-nums">{value}</div>
      <div className="mt-[2px] text-[10.5px] text-ink-subtle">{label}</div>
    </div>
  );
}

// Card 5: the run itself — what it would do, whether it may start, how far it
// has got, and the live log underneath.
//
// Zero hooks besides `useTranslation`: every request lives on the page, like its
// four sibling cards.
export function LaunchCard({
  campaign,
  run,
  pool,
  targets,
  roles,
  steps,
  logLines,
  onStart,
  onStop,
  onClearLogs,
  busy,
}: {
  campaign: NeuroshillingCampaign;
  run: NeuroshillingRunStatus;
  // The whole pool, so halted account ids can be named; the roster is the
  // `assigned` subset of it.
  pool: NeuroshillingBoardAccount[];
  targets: string[];
  roles: NeuroshillingRole[];
  steps: NeuroshillingStep[];
  logLines: LogEntry[];
  onStart: () => void;
  onStop: () => void;
  onClearLogs: () => void;
  busy: boolean;
}) {
  const { t } = useTranslation();
  const roster = pool.filter((account) => account.assigned);
  const status = run.status ?? 'idle';
  const live = status === 'running' || status === 'stopping';
  const scenarioStatus = campaign.scenario_status ?? 'draft';
  const blockers = launchBlockers(t, campaign, roster, targets, roles, steps);
  const messageSteps = steps.filter((step) => step.kind === 'message').length;
  const sent = run.sent ?? 0;
  const total = run.total ?? 0;
  const percent = total === 0 ? 0 : Math.min(100, Math.round((sent / total) * 100));
  const titleOf = (accountId: string) =>
    pool.find((account) => account.account_id === accountId)?.title ?? accountId;
  const halted = run.halted_accounts ?? [];

  return (
    <CollapsibleCard
      defaultOpen
      label={t('neuroshilling.launch.title')}
      headerClassName="px-4 py-[15px]"
      bodyClassName="px-4 pb-[15px]"
      header={
        <>
          <span className="text-[13px] font-semibold">{t('neuroshilling.launch.title')}</span>
          {live ? (
            <span className="inline-flex items-center gap-[5px] rounded-full bg-success-tint px-[9px] py-[2px] text-[10.5px] font-bold text-success">
              <span className="tb-livedot h-[6px] w-[6px] rounded-full bg-success" />
              LIVE
            </span>
          ) : null}
        </>
      }
      trailing={
        // The approval dies THREE cards up — any role or step edit returns the
        // campaign to `draft` — and the consequence only shows here, at launch.
        // Repeating the badge is what stops that being a surprise 409.
        <span
          className={`shrink-0 rounded-full px-[10px] py-[3px] text-[11px] font-semibold ${scenarioStatus === 'approved' ? 'bg-success-tint text-success' : 'bg-[#f4f3f0] text-ink-muted'}`}
        >
          {t(`neuroshilling.launch.scenario.${scenarioStatus}`)}
        </span>
      }
    >
      <div className="mb-[12px] grid grid-cols-3 gap-[8px] sm:grid-cols-5">
        <Tile label={t('neuroshilling.launch.tile.accounts')} value={String(roster.length)} />
        <Tile label={t('neuroshilling.launch.tile.targets')} value={String(targets.length)} />
        <Tile label={t('neuroshilling.launch.tile.roles')} value={String(roles.length)} />
        <Tile label={t('neuroshilling.launch.tile.messages')} value={String(messageSteps)} />
        <Tile
          label={t('neuroshilling.launch.tile.dialogue')}
          value={clock(dialogueSeconds(steps))}
        />
      </div>

      <div className="mb-[7px] flex flex-wrap items-center gap-[8px]">
        <span
          className="inline-flex items-center gap-[5px] text-[11.5px] font-medium"
          style={{ color: STATUS_COLOR[status] }}
        >
          <span
            className="h-[6px] w-[6px] rounded-full"
            style={{ background: STATUS_COLOR[status] }}
          />
          {t(`neuroshilling.campaign.status.${status}`)}
        </span>
        {/* Counted over the whole campaign rather than this run, because the roster
            row a substitution writes is the campaign's and outlives the run. Shown at
            zero too: "nobody has been replaced" is the answer the operator is
            checking for. */}
        <span className="rounded-full bg-[#f4f3f0] px-[9px] py-[2px] text-[11px] font-medium tabular-nums text-ink-muted">
          {t('neuroshilling.launch.substitutions', { n: run.substitutions ?? 0 })}
        </span>
        {/* `sent` / `total` counts MESSAGE steps only: reactions are journalled but
            a skipped reaction is not lost progress, so presenting the bar as
            counting every step would make it lie downward. */}
        <span className="ml-auto text-[11.5px] tabular-nums text-ink-subtle">
          {t('neuroshilling.launch.progress', { sent, total })}
        </span>
      </div>
      <div
        role="progressbar"
        aria-label={t('neuroshilling.launch.progressLabel')}
        aria-valuemin={0}
        aria-valuemax={total}
        aria-valuenow={sent}
        className="mb-[12px] h-[7px] w-full overflow-hidden rounded-full bg-[#eceae6]"
      >
        <div
          className="h-full rounded-full bg-primary transition-[width] duration-500"
          style={{ width: `${String(percent)}%` }}
        />
      </div>

      {/* Shown only while the run is really reading: the three switches are on the
          campaign row already, and what the operator cannot see from there is
          whether anything is acting on them right now. */}
      {run.listening === true ? (
        <div className="mb-[12px] flex flex-wrap items-center gap-[8px] rounded-[10px] bg-[#f4f3f0] px-[11px] py-[7px] text-[11.5px] tabular-nums text-ink-muted">
          <span className="font-medium">{t('neuroshilling.launch.listening')}</span>
          <span>{t('neuroshilling.launch.chatSeen', { n: run.chat_messages_seen ?? 0 })}</span>
          <span>{t('neuroshilling.launch.humanReplies', { n: run.human_replies_sent ?? 0 })}</span>
        </div>
      ) : null}

      {status === 'failed' && run.last_error_type ? (
        <div className="mb-[10px] rounded-[10px] bg-danger-tint px-[11px] py-[7px] text-[11.5px] text-danger">
          {t('neuroshilling.launch.failed', { type: run.last_error_type })}
        </div>
      ) : null}

      {halted.length > 0 ? (
        <div className="mb-[10px] rounded-[10px] bg-[#fdf4e3] px-[11px] py-[7px] text-[11.5px] text-warning">
          {t('neuroshilling.launch.halted', { names: halted.map(titleOf).join(', ') })}
        </div>
      ) : null}

      {!live && blockers.length > 0 ? (
        // Every reason, not just the first: fixing one only to be refused by the
        // next is the loop this list exists to end.
        <ul className="mb-[10px] flex list-none flex-col gap-[4px] rounded-[10px] bg-[#f4f3f0] px-[11px] py-[8px] text-[11.5px] text-ink-muted">
          {blockers.map((reason) => (
            <li key={reason}>· {reason}</li>
          ))}
        </ul>
      ) : null}

      <div className="mb-[14px] flex flex-wrap items-center justify-end gap-[8px]">
        {live ? (
          <button
            type="button"
            disabled={busy || status === 'stopping'}
            onClick={onStop}
            className="rounded-full border border-line-input bg-white px-[16px] py-[9px] text-[12.5px] font-semibold text-danger disabled:opacity-50"
          >
            {t('neuroshilling.launch.stop')}
          </button>
        ) : (
          <button
            type="button"
            disabled={busy || blockers.length > 0}
            onClick={onStart}
            className="rounded-full bg-primary px-[16px] py-[9px] text-[12.5px] font-semibold text-white disabled:opacity-50"
          >
            {t('neuroshilling.launch.start')}
          </button>
        )}
      </div>

      <LogTerminal
        title={t('neuroshilling.launch.log')}
        logLines={logLines}
        onClear={onClearLogs}
        accountName={titleOf}
      />
    </CollapsibleCard>
  );
}
