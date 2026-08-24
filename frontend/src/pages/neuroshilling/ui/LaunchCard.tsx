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
import { Badge, Button, CollapsibleCard } from '@/shared/ui';
import { LogTerminal } from '@/widgets/log-terminal';

import { clock, dialogueSeconds } from './scenarioDraft';

// The minimum a dialogue needs: one account per voice, and a monologue is not a
// dialogue. It is the shipped `NEUROSHILLING__MIN_ACCOUNTS` default and it is NOT
// on the wire — the board carries no policy numbers — so the 409 stays the
// authority and this only decides what the operator is told BEFORE clicking. The
// accounts card's hint states the same two.
const MIN_ACCOUNTS = 2;

// Tone is the token the status MEANS, matching the campaigns card row for row.
const STATUS_TONE: Record<NonNullable<NeuroshillingRunStatus['status']>, string> = {
  idle: 'text-ink-muted',
  running: 'text-success-deep',
  stopping: 'text-warning-deep',
  done: 'text-primary',
  failed: 'text-danger',
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
    <div className="rounded-lg border border-line bg-surface px-md py-md text-center">
      <div className="text-title font-bold tabular-nums">{value}</div>
      <div className="mt-hair text-micro text-ink-subtle">{label}</div>
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
  // A revive campaign loops until it is stopped, so the server sends no total and
  // there is nothing to be a fraction of. The counter replaces the bar rather than
  // sitting beside an empty one.
  const looping = campaign.mode === 'revive';
  const titleOf = (accountId: string) =>
    pool.find((account) => account.account_id === accountId)?.title ?? accountId;
  const halted = run.halted_accounts ?? [];

  return (
    <CollapsibleCard
      defaultOpen
      label={t('neuroshilling.launch.title')}
      headerClassName="px-lg py-lg"
      bodyClassName="px-lg pb-lg"
      header={
        <>
          <span className="text-lead font-semibold">{t('neuroshilling.launch.title')}</span>
          {live ? (
            // An accent marker, not a neutral state label — `micro`/`bold` on purpose, so
            // it reads as emphasis beside the title rather than as another status pill.
            // Two such markers in the app (the other is the warming page's "прогрет"), and
            // two call sites do not earn a token. Do not fold this into the pill family.
            <span className="inline-flex items-center gap-tight rounded-full bg-success-tint px-md py-hair text-micro font-bold text-success-deep">
              <span className="tb-livedot size-dot rounded-full bg-success" />
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
          className={`shrink-0 rounded-full px-md py-xs text-tiny font-semibold ${scenarioStatus === 'approved' ? 'bg-success-tint text-success-deep' : 'bg-canvas text-ink-muted'}`}
        >
          {t(`neuroshilling.launch.scenario.${scenarioStatus}`)}
        </span>
      }
    >
      <div className="mb-md grid grid-cols-3 gap-sm sm:grid-cols-5">
        <Tile label={t('neuroshilling.launch.tile.accounts')} value={String(roster.length)} />
        <Tile label={t('neuroshilling.launch.tile.targets')} value={String(targets.length)} />
        <Tile label={t('neuroshilling.launch.tile.roles')} value={String(roles.length)} />
        <Tile label={t('neuroshilling.launch.tile.messages')} value={String(messageSteps)} />
        <Tile
          label={t('neuroshilling.launch.tile.dialogue')}
          value={clock(dialogueSeconds(steps))}
        />
      </div>

      <div className="mb-sm flex flex-wrap items-center gap-sm">
        <span
          className={`inline-flex items-center gap-tight text-tiny font-medium ${STATUS_TONE[status]}`}
        >
          {/* `bg-current` — the dot can never disagree with its label. */}
          <span className="size-dot rounded-full bg-current" />
          {t(`neuroshilling.campaign.status.${status}`)}
        </span>
        {/* Counted over the whole campaign rather than this run, because the roster
            row a substitution writes is the campaign's and outlives the run. Shown at
            zero too: "nobody has been replaced" is the answer the operator is
            checking for. */}
        <Badge className="tabular-nums">
          {t('neuroshilling.launch.substitutions', { n: run.substitutions ?? 0 })}
        </Badge>
        {/* `sent` / `total` counts MESSAGE steps only: reactions are journalled but
            a skipped reaction is not lost progress, so presenting the bar as
            counting every step would make it lie downward. */}
        <span className="ml-auto text-tiny tabular-nums text-ink-subtle">
          {t(looping ? 'neuroshilling.launch.sentTotal' : 'neuroshilling.launch.progress', {
            sent,
            total,
          })}
        </span>
      </div>
      {looping ? null : (
        <div
          role="progressbar"
          aria-label={t('neuroshilling.launch.progressLabel')}
          aria-valuemin={0}
          aria-valuemax={total}
          aria-valuenow={sent}
          className="mb-md h-meter w-full overflow-hidden rounded-full bg-canvas"
        >
          <div
            className="h-full rounded-full bg-primary transition-[width] duration-reveal"
            style={{ width: `${String(percent)}%` }}
          />
        </div>
      )}

      {/* Shown only while the run is really reading: the three switches are on the
          campaign row already, and what the operator cannot see from there is
          whether anything is acting on them right now. */}
      {run.listening === true ? (
        <div className="mb-md flex flex-wrap items-center gap-sm rounded-lg bg-canvas px-md py-sm text-tiny tabular-nums text-ink-muted">
          <span className="font-medium">{t('neuroshilling.launch.listening')}</span>
          <span>{t('neuroshilling.launch.chatSeen', { n: run.chat_messages_seen ?? 0 })}</span>
          <span>{t('neuroshilling.launch.humanReplies', { n: run.human_replies_sent ?? 0 })}</span>
        </div>
      ) : null}

      {status === 'failed' && run.last_error_type ? (
        <div className="mb-md rounded-lg bg-danger-tint px-md py-sm text-tiny text-danger-deep">
          {t('neuroshilling.launch.failed', { type: run.last_error_type })}
        </div>
      ) : null}

      {halted.length > 0 ? (
        <div className="mb-md rounded-lg bg-warning-tint px-md py-sm text-tiny text-warning-deep">
          {t('neuroshilling.launch.halted', { names: halted.map(titleOf).join(', ') })}
        </div>
      ) : null}

      {!live && blockers.length > 0 ? (
        // Every reason, not just the first: fixing one only to be refused by the
        // next is the loop this list exists to end.
        <ul className="mb-md flex list-none flex-col gap-xs rounded-lg bg-canvas px-md py-sm text-tiny text-ink-muted">
          {blockers.map((reason) => (
            <li key={reason}>· {reason}</li>
          ))}
        </ul>
      ) : null}

      <div className="mb-lg flex flex-wrap items-center justify-end gap-sm">
        {live ? (
          <Button
            size="sm"
            className="text-danger"
            disabled={busy || status === 'stopping'}
            onClick={onStop}
          >
            {t('neuroshilling.launch.stop')}
          </Button>
        ) : (
          <Button
            variant="primary"
            size="sm"
            disabled={busy || blockers.length > 0}
            onClick={onStart}
          >
            {t('neuroshilling.launch.start')}
          </Button>
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
