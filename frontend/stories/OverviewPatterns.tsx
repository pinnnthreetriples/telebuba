import { useRef, useState } from 'react';

import { Section } from '../catalog/Frame';
import { AccountAvatar } from '../src/entities/account';
import {
  CampaignDeleteModal,
  CampaignPromptModal,
  CreateCampaignModal,
  type PromptAccount,
} from '../src/entities/campaign';
import { CampaignsCard } from '../src/pages/neurocomment/ui/CampaignsCard';
import { PipelineCard as RuntimePipeline } from '../src/pages/neurocomment/ui/PipelineCard';
import { PipelineCard as LaunchPipeline } from '../src/pages/neuroshilling/ui/PipelineCard';
import type {
  LogEntry,
  NeurocommentCampaign,
  NeuroshillingBoardAccount,
  NeuroshillingCampaign,
  NeuroshillingRole,
  NeuroshillingStep,
} from '../src/shared/api';
import {
  Badge,
  Button,
  ConfirmModal,
  InlineChipEditor,
  Modal,
  NumberedStep,
  TabList,
} from '../src/shared/ui';
import { LogTerminal } from '../src/widgets/log-terminal';
import { RetryNotice } from '../src/widgets/account-edit/ui/RetryNotice';

const logLines: LogEntry[] = [
  {
    id: 1,
    created_at: '2026-08-28T12:00:00Z',
    level: 'INFO',
    status: 'success',
    account_id: 'acc-1',
    event: 'neuroshilling_message_sent',
    extra: { channel: '@crypto_daily' },
  },
  {
    id: 2,
    created_at: '2026-08-28T12:01:00Z',
    level: 'WARNING',
    status: 'warning',
    account_id: 'acc-2',
    event: 'neuroshilling_run_stopped',
    extra: {},
  },
];

const campaignList: NeurocommentCampaign[] = [
  {
    campaign_id: 'nc-0',
    name: 'Крипта',
    prompt: 'Короткий дружелюбный комментарий по теме поста, без ссылок.',
    status: 'active',
    created_at: '2026-08-28T12:00:00Z',
    updated_at: '2026-08-28T12:00:00Z',
    channel_count: 4,
    account_count: 3,
  },
  {
    campaign_id: 'nc-1',
    name: 'Путешествия',
    prompt: 'Короткий дружелюбный комментарий по теме поста, без ссылок.',
    status: 'paused',
    created_at: '2026-08-28T12:00:00Z',
    updated_at: '2026-08-28T12:00:00Z',
    channel_count: 2,
    account_count: 1,
  },
];

const campaign: NeuroshillingCampaign = {
  campaign_id: 'c1',
  name: 'Запуск токена',
  mode: 'campaign',
  scenario_status: 'approved',
  run_mode: 'sequential',
  status: 'idle',
  created_at: '2026-08-28T12:00:00Z',
  updated_at: '2026-08-28T12:00:00Z',
};
const roles: NeuroshillingRole[] = [
  { role_id: 'r1', name: 'Скептик', created_at: '2026-08-28T12:00:00Z' },
  { role_id: 'r2', name: 'Сторонник', created_at: '2026-08-28T12:00:00Z' },
];
const steps: NeuroshillingStep[] = [
  {
    step_id: 's1',
    position: 1,
    kind: 'message',
    role_id: 'r1',
    delay_min_seconds: 60,
    delay_max_seconds: 180,
  },
  {
    step_id: 's2',
    position: 2,
    kind: 'message',
    role_id: 'r2',
    delay_min_seconds: 30,
    delay_max_seconds: 90,
  },
];
const pool: NeuroshillingBoardAccount[] = [
  { account_id: 'a1', title: 'Иван Петров', assigned: true, role_id: 'r1' },
  { account_id: 'a2', title: 'Мария Смирнова', assigned: true, role_id: 'r2' },
];

const campaignAccounts: Record<string, PromptAccount[]> = {
  'nc-0': [
    { account_id: 'a1', phone: '+7 900 111-22-33', channel: '@crypto_daily', initials: 'ИП' },
    { account_id: 'a2', phone: '+7 900 444-55-66', channel: '@defi_news', initials: 'МС' },
    { account_id: 'a3', phone: '+7 900 777-88-99', channel: '@crypto_daily', initials: 'ПК' },
  ],
  'nc-1': [
    { account_id: 'a4', phone: '+7 900 222-33-44', channel: '@travel_news', initials: 'АК' },
  ],
};

export function OverviewPatterns() {
  const nextCampaignId = useRef(2);
  const [campaigns, setCampaigns] = useState(campaignList);
  const [campaignId, setCampaignId] = useState<string | null>('nc-0');
  const [openCampaignActions, setOpenCampaignActions] = useState<string | null>(null);
  const [createCampaignOpen, setCreateCampaignOpen] = useState(false);
  const [promptFor, setPromptFor] = useState<NeurocommentCampaign | null>(null);
  const [deleteFor, setDeleteFor] = useState<NeurocommentCampaign | null>(null);
  const [discoveryOpen, setDiscoveryOpen] = useState(false);
  const [accountsByCampaign, setAccountsByCampaign] = useState(campaignAccounts);
  const [tab, setTab] = useState<'text' | 'photo'>('text');
  const [chipValue, setChipValue] = useState('');
  const [channels, setChannels] = useState(['@crypto_daily']);
  const [channelsByCampaign, setChannelsByCampaign] = useState<
    Record<string, { channel: string; deleted_recent?: number }[]>
  >({
    'nc-0': [{ channel: '@crypto_daily' }, { channel: '@defi_news', deleted_recent: 1 }],
    'nc-1': [{ channel: '@travel_news' }],
  });
  const [addingCampaignChannel, setAddingCampaignChannel] = useState(false);
  const [campaignChannelInput, setCampaignChannelInput] = useState('');
  const [channelChecks, setChannelChecks] = useState<Record<string, 'banned' | 'ok'>>({});
  const [removeChannelFor, setRemoveChannelFor] = useState<string | null>(null);
  const [entries, setEntries] = useState(logLines);
  const [confirmClearLogs, setConfirmClearLogs] = useState(false);
  const [retryNoticeVisible, setRetryNoticeVisible] = useState(true);
  const [runtimeRunning, setRuntimeRunning] = useState(true);
  const [launchRunning, setLaunchRunning] = useState(false);
  const campaignChannels = channelsByCampaign[campaignId ?? ''] ?? [];

  return (
    <Section
      id="patterns"
      title="Блоки продукта"
      note="Блок кампаний взят с экрана нейрокомментинга. Карточка выбора и журнал используются в двух разделах; конвейеры показывают разные процессы и поэтому остаются отдельными блоками. Поиск каналов здесь показывает модальную оболочку; сам поиск требует сервер."
    >
      <div className="grid gap-lg lg:grid-cols-2">
        <div className="min-w-0">
          <h3 className="mb-sm type-card-title">Блок кампаний</h3>
          <p className="mb-md type-caption">Нейрокомментинг</p>
          <div className="grid lg:grid-cols-[340px_minmax(0,1fr)]">
            <CampaignsCard
              campaignList={campaigns}
              campaignId={campaignId}
              activeCampaign={campaigns.find((item) => item.campaign_id === campaignId) ?? null}
              boardChannels={campaignChannels}
              openCampaignActions={openCampaignActions}
              onToggleActions={(id) =>
                setOpenCampaignActions((current) => (current === id ? null : id))
              }
              onSelect={(id) => {
                setCampaignId(id);
                setChannelChecks({});
              }}
              onToggleStatus={(item) =>
                setCampaigns((current) =>
                  current.map((campaign) =>
                    campaign.campaign_id === item.campaign_id
                      ? { ...campaign, status: campaign.status === 'active' ? 'paused' : 'active' }
                      : campaign,
                  ),
                )
              }
              onEditPrompt={(item) => {
                setCampaignId(item.campaign_id);
                setPromptFor(item);
              }}
              onDelete={setDeleteFor}
              onCreate={() => setCreateCampaignOpen(true)}
              channelFeedback={{}}
              addingChannel={addingCampaignChannel}
              onStartAdd={() => setAddingCampaignChannel(true)}
              onCancelAdd={() => {
                setAddingCampaignChannel(false);
                setCampaignChannelInput('');
              }}
              channelInput={campaignChannelInput}
              onChannelInput={setCampaignChannelInput}
              onAddChannel={() => {
                const channel = campaignChannelInput.trim();
                if (!channel) return;
                if (campaignId === null) return;
                setChannelsByCampaign((current) => ({
                  ...current,
                  [campaignId]: [...(current[campaignId] ?? []), { channel }],
                }));
                setCampaigns((current) =>
                  current.map((item) =>
                    item.campaign_id === campaignId
                      ? { ...item, channel_count: (item.channel_count ?? 0) + 1 }
                      : item,
                  ),
                );
                setCampaignChannelInput('');
                setAddingCampaignChannel(false);
              }}
              onRemoveChannel={setRemoveChannelFor}
              onCheckChannels={() => {
                setChannelChecks(
                  Object.fromEntries(
                    campaignChannels.map(({ channel }) => [
                      channel,
                      channel === '@defi_news' ? 'banned' : 'ok',
                    ]),
                  ),
                );
              }}
              checkingChannels={false}
              channelCheckStatus={channelChecks}
              discoverySlot={
                <Button
                  size="xs"
                  onClick={() => setDiscoveryOpen(true)}
                  className="text-tiny text-content-muted hover:border-action-primary hover:text-action-primary"
                >
                  Найти каналы
                </Button>
              }
            />
            {discoveryOpen && (
              <Modal onClose={() => setDiscoveryOpen(false)} size="table" label="Поиск каналов">
                <div className="border-b border-line-row px-2xl pb-lg pt-xl">
                  <h2 className="type-dialog-title">Поиск каналов</h2>
                  <p className="mt-hair type-caption">
                    Для кампании {campaigns.find((item) => item.campaign_id === campaignId)?.name}
                  </p>
                </div>
                <div className="px-2xl py-xl type-prose">
                  Поиск и результаты доступны в подключённом приложении.
                </div>
                <div className="flex justify-end border-t border-line-row px-2xl py-lg">
                  <Button onClick={() => setDiscoveryOpen(false)}>Закрыть</Button>
                </div>
              </Modal>
            )}
            {removeChannelFor && (
              <ConfirmModal
                title={`Удалить ${removeChannelFor}?`}
                body="Канал будет удалён из кампании."
                confirmLabel="Удалить"
                cancelLabel="Отмена"
                onClose={() => setRemoveChannelFor(null)}
                onConfirm={() => {
                  if (campaignId === null) return;
                  setChannelsByCampaign((current) => ({
                    ...current,
                    [campaignId]: (current[campaignId] ?? []).filter(
                      (item) => item.channel !== removeChannelFor,
                    ),
                  }));
                  setCampaigns((current) =>
                    current.map((item) =>
                      item.campaign_id === campaignId
                        ? { ...item, channel_count: Math.max((item.channel_count ?? 0) - 1, 0) }
                        : item,
                    ),
                  );
                  setChannelChecks({});
                }}
              />
            )}
            {createCampaignOpen && (
              <CreateCampaignModal
                onClose={() => setCreateCampaignOpen(false)}
                onCreate={({ name, prompt, channels: newChannels }) => {
                  const id = `nc-${String(nextCampaignId.current++)}`;
                  const now = new Date().toISOString();
                  setCampaigns((current) => [
                    ...current,
                    {
                      campaign_id: id,
                      name,
                      prompt,
                      status: 'paused',
                      created_at: now,
                      updated_at: now,
                      channel_count: newChannels.length,
                      account_count: 0,
                    },
                  ]);
                  setChannelsByCampaign((current) => ({
                    ...current,
                    [id]: newChannels.map((channel) => ({ channel })),
                  }));
                  setCampaignId(id);
                  setChannelChecks({});
                }}
              />
            )}
            {promptFor && (
              <CampaignPromptModal
                campaignName={promptFor.name}
                initialPrompt={promptFor.prompt}
                accounts={accountsByCampaign[promptFor.campaign_id] ?? []}
                onClose={() => setPromptFor(null)}
                onSave={(prompt) =>
                  setCampaigns((current) =>
                    current.map((item) =>
                      item.campaign_id === promptFor.campaign_id ? { ...item, prompt } : item,
                    ),
                  )
                }
                onRemoveAccount={(accountId) => {
                  setAccountsByCampaign((current) => ({
                    ...current,
                    [promptFor.campaign_id]: (current[promptFor.campaign_id] ?? []).filter(
                      (item) => item.account_id !== accountId,
                    ),
                  }));
                  setCampaigns((current) =>
                    current.map((item) =>
                      item.campaign_id === promptFor.campaign_id
                        ? { ...item, account_count: Math.max((item.account_count ?? 0) - 1, 0) }
                        : item,
                    ),
                  );
                }}
              />
            )}
            {deleteFor && (
              <CampaignDeleteModal
                name={deleteFor.name}
                onClose={() => setDeleteFor(null)}
                onConfirm={() => {
                  setCampaigns((current) =>
                    current.filter((item) => item.campaign_id !== deleteFor.campaign_id),
                  );
                  setCampaignId((current) =>
                    current === deleteFor.campaign_id
                      ? (campaigns.find((item) => item.campaign_id !== deleteFor.campaign_id)
                          ?.campaign_id ?? null)
                      : current,
                  );
                  setOpenCampaignActions(null);
                  setChannelChecks({});
                }}
              />
            )}
          </div>
        </div>
        <div className="min-w-0">
          <h3 className="mb-sm type-card-title">Аватар аккаунта</h3>
          <p className="mb-md type-caption">Таблица аккаунтов, прогрев и формы выбора</p>
          <div className="flex items-center gap-xl rounded-card border border-line bg-surface-card p-lg">
            <div className="flex items-center gap-sm">
              <AccountAvatar
                account={{ account_id: 'a1', first_name: 'Иван', last_name: 'Петров' }}
                className="size-tile shrink-0 rounded-full"
                fallbackClassName="bg-info-tint text-info-strong text-body font-semibold"
              />
              <span className="type-caption">В таблице</span>
            </div>
            <div className="flex items-center gap-sm">
              <AccountAvatar
                account={{ account_id: 'a2', first_name: 'Мария', last_name: 'Смирнова' }}
                className="size-tile shrink-0 rounded-full ring-2 ring-success"
                fallbackClassName="bg-info-tint text-info-strong text-tiny font-bold"
              />
              <span className="type-caption">В прогреве</span>
            </div>
          </div>
        </div>
      </div>

      <div className="grid gap-lg lg:grid-cols-2">
        <div className="min-w-0">
          <h3 className="mb-sm type-card-title">Вкладки профиля</h3>
          <p className="mb-md type-caption">Профиль и массовое редактирование аккаунтов</p>
          <div className="rounded-card border border-line bg-surface-card">
            <TabList
              options={[
                { value: 'text', label: 'Текст' },
                { value: 'photo', label: 'Фото' },
              ]}
              value={tab}
              onChange={setTab}
              idPrefix="overview-tab"
              panelId="overview-tabpanel"
              ariaLabel="Разделы профиля"
            />
            <div
              role="tabpanel"
              id="overview-tabpanel"
              aria-labelledby={`overview-tab-${tab}`}
              className="p-lg type-prose"
            >
              {tab === 'text' ? 'Имя, фамилия и описание' : 'Фото аккаунта'}
            </div>
          </div>
        </div>
        <div className="min-w-0">
          <h3 className="mb-sm type-card-title">Редактор канала</h3>
          <p className="mb-md type-caption">Прогрев, нейрокомментинг и нейрошиллинг</p>
          <div className="flex flex-wrap items-center gap-sm rounded-card border border-line bg-surface-card p-lg">
            {channels.map((channel) => (
              <Badge key={channel} tone="neutral">
                {channel}
              </Badge>
            ))}
            <InlineChipEditor
              value={chipValue}
              onChange={setChipValue}
              onConfirm={() => {
                if (!chipValue.trim()) return;
                setChannels((current) => [...current, chipValue.trim()]);
                setChipValue('');
              }}
              onCancel={() => setChipValue('')}
              placeholder="@channel"
              inputLabel="Новый канал"
              confirmLabel="Добавить канал"
              cancelLabel="Отмена"
            />
          </div>
        </div>
      </div>

      <div>
        <h3 className="mb-sm type-card-title">Нумерованные шаги</h3>
        <p className="mb-md type-caption">Подсказки прогрева и кампаний</p>
        <div className="grid gap-md md:grid-cols-2">
          <NumberedStep number={1}>Выберите аккаунты.</NumberedStep>
          <NumberedStep number={2}>Проверьте ограничения.</NumberedStep>
        </div>
      </div>

      <div>
        <h3 className="mb-sm type-card-title">Ошибка загрузки с повтором</h3>
        <p className="mb-md type-caption">Редактирование аккаунта и его каналов</p>
        {retryNoticeVisible && (
          <RetryNotice
            message="Не удалось загрузить настройки аккаунта."
            label="Повторить"
            role="alert"
            onRetry={() => setRetryNoticeVisible(false)}
          />
        )}
      </div>

      <div>
        <h3 className="mb-sm type-card-title">Журнал событий</h3>
        <p className="mb-md type-caption">Нейрокомментинг и нейрошиллинг</p>
        <LogTerminal
          title="Лог кампании"
          logLines={entries}
          accountName={(id) => (id === 'acc-1' ? 'Иван Петров' : 'Мария Смирнова')}
          onClear={() => setConfirmClearLogs(true)}
        />
        {confirmClearLogs && (
          <ConfirmModal
            title="Очистить журнал?"
            body="Записи журнала будут удалены."
            confirmLabel="Очистить"
            cancelLabel="Отмена"
            onClose={() => setConfirmClearLogs(false)}
            onConfirm={() => setEntries([])}
          />
        )}
      </div>

      <div>
        <h3 className="mb-sm type-card-title">Конвейер обработки</h3>
        <p className="mb-md type-caption">Текущая стадия нейрокомментинга</p>
        <RuntimePipeline
          running={runtimeRunning}
          canStart
          events={[]}
          onToggle={() => setRuntimeRunning((current) => !current)}
          stats={[
            { label: 'Кампаний', value: 2, color: 'text-content-primary' },
            { label: 'Каналов', value: 4, color: 'text-action-primary' },
            { label: 'Аккаунтов', value: 2, color: 'text-content-primary' },
            { label: 'Комментариев', value: 14, color: 'text-success-deep' },
            { label: 'Удалено', value: 1, color: 'text-danger' },
            { label: 'Ошибок', value: 2, color: 'text-danger' },
          ]}
        />
      </div>

      <div>
        <h3 className="mb-sm type-card-title">Готовность к запуску</h3>
        <p className="mb-md type-caption">Проверки кампании нейрошиллинга</p>
        <LaunchPipeline
          campaign={campaign}
          run={{ status: launchRunning ? 'running' : 'idle', sent: 0, total: 120 }}
          pool={pool}
          targets={['@crypto_daily', '@defi_news']}
          roles={roles}
          steps={steps}
          onStart={() => setLaunchRunning(true)}
          onStop={() => setLaunchRunning(false)}
          busy={false}
        />
      </div>
    </Section>
  );
}
