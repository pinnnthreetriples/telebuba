import type { TFunction } from 'i18next';

import type {
  NeuroshillingBoardAccount,
  NeuroshillingCampaign,
  NeuroshillingRole,
  NeuroshillingStep,
} from '@/shared/api';

// The minimum a dialogue needs: one account per voice, and a monologue is not a
// dialogue. It is the shipped `NEUROSHILLING__MIN_ACCOUNTS` default and it is NOT
// on the wire — the board carries no policy numbers — so the 409 stays the
// authority and this only decides what the operator is told BEFORE clicking. The
// accounts card's hint states the same two.
const MIN_ACCOUNTS = 2;

/** Why Start is refused, in the SERVER's order, or an empty list.
 *
 * Mirrors `services.neuroshilling._runtime.start_campaign` check for check so the
 * operator reads the reason instead of collecting a 409 whose body is a bare
 * code. Where the two can disagree the server wins: this is a courtesy, not a
 * gate — which is why it is allowed to be approximate about `MIN_ACCOUNTS` and
 * exact about everything the board actually carries.
 *
 * Отдельный модуль, а не приватная функция карточки: список читают теперь ДВА места —
 * сводка замечаний в сайдбаре, которая называет их число до того, как оператор дойдёт до
 * кнопки, и сам конвейер, который в эту кнопку и упирается. Две копии разошлись бы ровно
 * в тот момент, когда на сервере появится седьмая причина.
 */
export function launchBlockers(
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
