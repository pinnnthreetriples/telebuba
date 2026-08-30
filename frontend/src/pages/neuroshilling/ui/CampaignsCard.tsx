import { useTranslation } from 'react-i18next';

import type { NeuroshillingCampaign } from '@/shared/api';
import { FOCUS_RING } from '@/shared/design-system';
import { cn } from '@/shared/lib/cn';
import { Button, CollapsibleCard, Icon, IconButton, SurfHover } from '@/shared/ui';

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
            <SurfHover
              key={campaign.campaign_id}
              surfaceId={`ns-camp-${campaign.campaign_id}`}
              open={openActions === campaign.campaign_id}
              actions={
                <>
                  <button
                    type="button"
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
                    className={`flex w-action items-center justify-center border-none bg-transparent ${isRunning ? 'text-warning-deep' : 'text-success-deep'}`}
                  >
                    <Icon name={isRunning ? 'pause' : 'play'} size={18} />
                  </button>
                  <button
                    type="button"
                    title={t('neuroshilling.campaign.settings')}
                    aria-label={t('neuroshilling.campaign.settings')}
                    onClick={() => {
                      onSettings(campaign.campaign_id);
                    }}
                    className="flex w-action items-center justify-center border-none bg-transparent text-action-primary"
                  >
                    <Icon name="pencil" size={18} />
                  </button>
                  <button
                    type="button"
                    title={t('neuroshilling.campaign.delete')}
                    aria-label={t('neuroshilling.campaign.delete')}
                    onClick={() => {
                      onDelete(campaign);
                    }}
                    className="flex w-action items-center justify-center border-none bg-transparent text-danger"
                  >
                    <Icon name="trash" size={18} />
                  </button>
                </>
              }
              surface={
                // Выбор — НАСТОЯЩАЯ кнопка во всю карточку, шестерёнка ей сосед, а не
                // потомок: вложенная кнопка недопустима в ARIA, а `div role="button"` не
                // получает Enter и Space бесплатно. Так же устроен ряд у неврокомментинга.
                <div
                  className={`relative rounded-lg border p-lg ${isSelected ? 'border-action-primary bg-info-tint' : 'border-line bg-surface-card'}`}
                >
                  <button
                    type="button"
                    aria-pressed={isSelected}
                    onClick={() => {
                      onSelect(campaign.campaign_id);
                    }}
                    aria-label={campaign.name}
                    className={cn('absolute inset-0 cursor-pointer rounded-lg', FOCUS_RING)}
                  />
                  <div className="pointer-events-none flex justify-between gap-md">
                    <div className="min-w-0 flex-1">
                      <div className="mb-tight truncate type-card-title">{campaign.name}</div>
                      {/* Только цели: счётчика аккаунтов у кампании на проводе НЕТ
                          (в отличие от неврокомментинга с его `account_count`), а ростер
                          приходит лишь для ВЫБРАННОЙ. Строка «5 аккаунтов» у одной
                          кампании и её отсутствие у соседних читались бы как «у тех
                          аккаунтов нет». */}
                      <div className="type-caption">
                        {t('neuroshilling.targetsCount', {
                          count: countTargets(campaign.targets_raw ?? ''),
                        })}
                      </div>
                    </div>
                    <div className="flex shrink-0 flex-col items-end gap-sm">
                      <CampaignStatusBadge plain status={status} />
                      {/* `pointer-events-auto` возвращает шестерёнке нажимаемость: слой
                          выше по стеку, поэтому её события не идут через кнопку выбора. */}
                      <span className="pointer-events-auto">
                        <IconButton
                          size="sm"
                          tone="primary"
                          aria-controls={`ns-camp-${campaign.campaign_id}`}
                          aria-expanded={openActions === campaign.campaign_id}
                          title={t('neuroshilling.campaign.actions')}
                          aria-label={t('neuroshilling.campaign.actions')}
                          onClick={() => {
                            onToggleActions(campaign.campaign_id);
                          }}
                        >
                          <Icon name="gear" size={14} />
                        </IconButton>
                      </span>
                    </div>
                  </div>
                </div>
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
          <input
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
            className="h-field min-w-0 flex-1 rounded-lg border border-action-primary bg-surface-card px-md text-body outline-none"
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
        <Button variant="dashed" size="block" className="mt-sm" onClick={onStartCreate}>
          {t('neuroshilling.campaigns.create')}
        </Button>
      )}
    </CollapsibleCard>
  );
}
