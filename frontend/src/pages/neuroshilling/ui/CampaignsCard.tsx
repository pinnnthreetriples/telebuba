import { useTranslation } from 'react-i18next';

import type { NeuroshillingCampaign } from '@/shared/api';
import { Button, CollapsibleCard, Icon, IconButton, Input, SelectableCard } from '@/shared/ui';

import { CampaignStatusBadge } from './CampaignStatusBadge';
import { countTargets } from './setupDraft';

// Список кампаний в сайдбаре: выбрать, создать, запустить, настроить, удалить.
//
// Карточка набрана ровно как у неврокомментинга — `p-lg`, имя рунгом `card-title`, мета
// под ним, статус и шестерёнка столбиком справа. Это одна и та же вещь на двух страницах,
// и мерить её двумя наборами значений можно было ровно до тех пор, пока их не поставили
// рядом.
//
// Хуков, кроме `useTranslation`, нет: каждое состояние и каждый запрос живут на странице,
// как и у соседей по неврокомментингу.
export function CampaignsCard({
  campaignList,
  campaignId,
  onSelect,
  onSettings,
  onDelete,
  onToggleStatus,
  openActions,
  onToggleActions,
  creating,
  createName,
  onStartCreate,
  onCancelCreate,
  onCreateName,
  onCreate,
}: {
  campaignList: NeuroshillingCampaign[];
  campaignId: string | null;
  onSelect: (campaignId: string) => void;
  // Карандаш строки: открыть настройки ЭТОЙ кампании. Выбор кампании он делает попутно —
  // редактировать невыбранную нельзя, все черновики страницы принадлежат выбранной.
  onSettings: (campaignId: string) => void;
  onDelete: (campaign: NeuroshillingCampaign) => void;
  // Запустить или остановить ЭТУ кампанию. Кнопка одна, и что она сделает, решает статус.
  onToggleStatus: (campaign: NeuroshillingCampaign) => void;
  // Идентификатор строки, чей слой действий раскрыт шестерёнкой, или `null`.
  //
  // Наведения мало: на касании его нет, а действия ЕСТЬ всегда — они не появляются, их
  // накрывает поверхность. Шестерёнка — тот же переключатель, что у неврокомментинга, и
  // раскрытая строка здесь ровно одна, поэтому это `id`, а не множество.
  openActions: string | null;
  onToggleActions: (campaignId: string) => void;
  creating: boolean;
  createName: string;
  onStartCreate: () => void;
  onCancelCreate: () => void;
  onCreateName: (value: string) => void;
  onCreate: () => void;
}) {
  const { t } = useTranslation();
  return (
    <CollapsibleCard
      defaultOpen
      label={t('neuroshilling.campaigns.title')}
      headerClassName="px-lg py-md"
      bodyClassName="px-lg pb-lg"
      header={<span className="type-card-title">{t('neuroshilling.campaigns.title')}</span>}
    >
      <div className="flex flex-col gap-tight">
        {campaignList.map((campaign) => {
          const isSelected = campaign.campaign_id === campaignId;
          const status = campaign.status ?? 'idle';
          const isRunning = status === 'running' || status === 'stopping';
          return (
            <SelectableCard
              key={campaign.campaign_id}
              surfaceId={`ns-camp-${campaign.campaign_id}`}
              name={campaign.name}
              meta={t('neuroshilling.targetsCount', {
                count: countTargets(campaign.targets_raw ?? ''),
              })}
              status={<CampaignStatusBadge plain status={status} />}
              selected={isSelected}
              actionsOpen={openActions === campaign.campaign_id}
              actionsLabel={t('neuroshilling.campaign.actions')}
              onSelect={() => {
                onSelect(campaign.campaign_id);
              }}
              onToggleActions={() => {
                onToggleActions(campaign.campaign_id);
              }}
              actions={
                <>
                  <IconButton
                    size="touch"
                    tone="neutral"
                    title={
                      isRunning
                        ? t('neuroshilling.campaign.pause')
                        : t('neuroshilling.campaign.run')
                    }
                    aria-label={
                      isRunning
                        ? t('neuroshilling.campaign.pause')
                        : t('neuroshilling.campaign.run')
                    }
                    onClick={() => {
                      onToggleStatus(campaign);
                    }}
                    className={`w-action self-stretch ${isRunning ? 'text-warning-deep hover:bg-warning-tint' : 'text-success-deep hover:bg-success-tint'}`}
                  >
                    <Icon name={isRunning ? 'pause' : 'play'} size={18} />
                  </IconButton>
                  <IconButton
                    size="touch"
                    tone="primary"
                    title={t('neuroshilling.campaign.settings')}
                    aria-label={t('neuroshilling.campaign.settings')}
                    onClick={() => {
                      onSettings(campaign.campaign_id);
                    }}
                    className="w-action self-stretch"
                  >
                    <Icon name="pencil" size={18} />
                  </IconButton>
                  <IconButton
                    size="touch"
                    tone="danger"
                    title={t('neuroshilling.campaign.delete')}
                    aria-label={t('neuroshilling.campaign.delete')}
                    onClick={() => {
                      onDelete(campaign);
                    }}
                    className="w-action self-stretch"
                  >
                    <Icon name="trash" size={18} />
                  </IconButton>
                </>
              }
            />
          );
        })}
        {campaignList.length === 0 ? (
          <div className="py-lg text-center type-prose">{t('neuroshilling.campaigns.none')}</div>
        ) : null}
      </div>

      {creating ? (
        // Строкой, а не диалогом: создание спрашивает имя и больше ничего, и приложение
        // уже пишет эту форму именно так (пилюля «добавить канал»).
        <div className="mt-sm flex items-center gap-sm">
          <Input
            size="sm"
            autoFocus
            value={createName}
            onChange={(event) => {
              onCreateName(event.target.value);
            }}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && createName.trim()) onCreate();
              if (event.key === 'Escape') onCancelCreate();
            }}
            placeholder={t('neuroshilling.campaigns.namePlaceholder')}
            aria-label={t('neuroshilling.campaigns.namePlaceholder')}
            className="min-w-0 flex-1 border-action-primary"
          />
          <Button variant="primary" size="sm" disabled={!createName.trim()} onClick={onCreate}>
            {t('neuroshilling.campaigns.confirm')}
          </Button>
          <IconButton
            size="sm"
            title={t('neuroshilling.campaigns.cancel')}
            aria-label={t('neuroshilling.campaigns.cancel')}
            onClick={onCancelCreate}
          >
            <Icon name="close" size={14} />
          </IconButton>
        </div>
      ) : (
        <Button variant="dashed" fullWidth className="mt-sm font-medium" onClick={onStartCreate}>
          {t('neuroshilling.campaigns.create')}
        </Button>
      )}
    </CollapsibleCard>
  );
}
