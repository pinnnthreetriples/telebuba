import { useTranslation } from 'react-i18next';

import { accountDisplayName } from '@/entities/account';
import type { AccountRead } from '@/shared/api';
import { Card, Icon, IconButton, Select, SurfHover } from '@/shared/ui';

// The listener-account card: shows the active listener with pause/edit/remove
// actions (revealed via SurfHover), or a dropdown to choose one when none is set.
export function ListenerCard({
  listenerId,
  running,
  activeCampaignCount,
  activeChannelCount,
  unwatchedChannels,
  listenerActionsOpen,
  onToggleActions,
  onToggleRuntime,
  onEdit,
  onRemove,
  accountOptions,
  onPickListener,
}: {
  listenerId: string;
  running: boolean;
  activeCampaignCount: number;
  activeChannelCount: number;
  unwatchedChannels: string[];
  listenerActionsOpen: boolean;
  onToggleActions: () => void;
  onToggleRuntime: () => void;
  onEdit: () => void;
  onRemove: () => void;
  accountOptions: AccountRead[];
  onPickListener: (accountId: string) => void;
}) {
  const { t } = useTranslation();
  // Green promised work that was not happening: an operator deleted their only campaign,
  // read the still-green plaque as "all fine", and came asking why it said «Слушает».
  //
  // The test is the WATCH SET, not the campaign count, because they come apart in three
  // reachable ways and the campaign count is green in all of them: an active campaign whose
  // channels were freed one at a time, a campaign created and not yet given any, and the
  // "up but deaf" state ``_lifecycle`` documents, where reconcile unsubscribed the listener
  // because its account is warming. This is also the number the card's own «Каналов» tile
  // shows, so the plaque can no longer contradict the tile beside it.
  //
  // The process itself stays up and the pause action stays where it is — the operator may
  // be between campaigns, and being switched off unasked is worse than a muted label.
  const working = running && activeChannelCount > 0;
  const statusLabel = working
    ? t('neurocomment.listener.listening')
    : running
      ? t('neurocomment.listener.listeningNoChannels')
      : t('neurocomment.listener.paused');
  return (
    <Card className="relative z-raised px-lg py-lg">
      <div className="mb-xs flex items-center gap-md">
        <span className="flex size-icon shrink-0 items-center justify-center rounded-lg bg-primary-tint text-primary-deep">
          <Icon name="chart" size={16} />
        </span>
        <div className="min-w-0">
          <div className="type-item-title">{t('neurocomment.listener.title')}</div>
        </div>
      </div>

      {listenerId ? (
        <div className="mt-md">
          <SurfHover
            shift={144}
            surfaceId="lsn-surf"
            open={listenerActionsOpen}
            actions={
              <>
                <button
                  type="button"
                  title={
                    running ? t('neurocomment.listener.pause') : t('neurocomment.listener.resume')
                  }
                  onClick={onToggleRuntime}
                  className={`flex w-action items-center justify-center border-none bg-transparent ${running ? 'text-warning-deep' : 'text-success-deep'}`}
                >
                  {running ? <Icon name="pause" size={16} /> : <Icon name="play" size={16} />}
                </button>
                <button
                  type="button"
                  title={t('neurocomment.listener.edit')}
                  onClick={onEdit}
                  className="flex w-action items-center justify-center border-none bg-transparent text-primary"
                >
                  <Icon name="pencil" size={16} />
                </button>
                <button
                  type="button"
                  title={t('neurocomment.listener.remove')}
                  onClick={onRemove}
                  className="flex w-action items-center justify-center border-none bg-transparent text-danger"
                >
                  <Icon name="trash" size={16} />
                </button>
              </>
            }
            surface={
              // Running = the success tone, idle = the neutral surface; both sides
              // come from tokens so the card can't drift from the rest of the design.
              <div
                className={`flex items-center justify-between gap-sm rounded-lg border px-md py-sm ${working ? 'border-success-line bg-success-tint' : 'border-line bg-surface'}`}
              >
                <div className="flex min-w-0 items-center gap-sm">
                  <span
                    className={`size-dot shrink-0 rounded-full ${working ? 'tb-livedot bg-success' : 'bg-ink-subtle'}`}
                  />
                  <span
                    className={`type-item-title ${working ? 'tb-pulse text-success-deep' : 'text-ink-muted'}`}
                  >
                    {statusLabel}
                  </span>
                  {/* Off the status-pill rung on purpose: a counter, not a state label. The
                      18px square minimum is what keeps a single digit CIRCULAR, and the
                      canon's `3px 10px` would stretch it into a lozenge at one digit. */}
                  <span
                    title={t('neurocomment.listener.activeCampaigns')}
                    className={`inline-flex h-badge min-w-badge shrink-0 items-center justify-center rounded-full px-tight text-micro font-bold text-white ${working ? 'bg-success-deep' : 'bg-ink-muted'}`}
                  >
                    {activeCampaignCount}
                  </span>
                </div>
                <IconButton
                  size="sm"
                  tone="primary"
                  title={t('neurocomment.listener.actions')}
                  aria-label={t('neurocomment.listener.actions')}
                  aria-expanded={listenerActionsOpen}
                  onClick={(event) => {
                    event.stopPropagation();
                    onToggleActions();
                  }}
                >
                  <Icon name="gear" size={14} />
                </IconButton>
              </div>
            }
          />
        </div>
      ) : (
        <div className="mt-md">
          <Select
            value=""
            onChange={onPickListener}
            options={accountOptions.map((account) => ({
              value: account.account_id,
              label: accountDisplayName(account),
            }))}
            placeholder={t('neurocomment.listener.choose')}
            ariaLabel={t('neurocomment.listener.title')}
            emptyLabel={t('neurocomment.listener.noAccounts')}
          />
        </div>
      )}

      {/* The listener silently drops a channel it cannot resolve, and the board still
          paints that channel `ready` — so this strip is the only place an operator can
          see that no post from it will ever arrive. Same note style as warmingBlocked. */}
      {unwatchedChannels.length > 0 ? (
        <p className="mt-sm type-caption font-medium text-danger">
          {t('neurocomment.listener.unwatched', {
            count: unwatchedChannels.length,
            channels: unwatchedChannels.join(', '),
          })}
        </p>
      ) : null}
    </Card>
  );
}
