// PROTOTYPE — throwaway. Variant D: the chat broadcast / neuroshilling page shape.
// Sidebar: campaigns and a folded «Как это работает». Main: the shared PipelineCard,
// the targets board, the log. Every setting lives in the campaign's dialog. The mock
// campaigns cover every lifecycle state, so the sidebar doubles as a state switcher.
import type { ColumnDef } from '@tanstack/react-table';
import { type ReactNode, useState } from 'react';

import { FOCUS_RING } from '@/shared/design-system';
import {
  Badge,
  Button,
  CollapsibleCard,
  DataTable,
  HelpHint,
  HowItWorksCard,
  Icon,
  IconButton,
  Input,
  PipelineCard,
  ProgressBar,
  SegmentedControl,
  SelectableCard,
} from '@/shared/ui';
import { LogTerminal } from '@/widgets/log-terminal';

import {
  ACTIVE_STATES,
  type BoardRow,
  boardRows,
  CAMPAIGNS,
  HOW_STEPS,
  LIVE_ID,
  logLines,
  type MockCampaign,
  MODE_SHORT,
  pipeline,
  plural,
  ROW,
  type Status,
  STATUS,
  targetsOf,
} from './campaigns';
import { ACCOUNTS, blockers, type Draft, estimate, type Patch } from './model';
import { PaceDialog, SettingsDialogD } from './SettingsDialogD';

export const NAME = 'Как рассылка и нейрошиллинг';

const HISTORY_TONE = {
  muted: 'text-content-muted',
  info: 'text-info-strong',
  warning: 'text-warning-deep',
  danger: 'text-danger',
} as const;

function AccountFace({ id }: { id: string | null }) {
  const account = id === null ? undefined : ACCOUNTS.find((a) => a.id === id);
  if (account === undefined) return <span className="text-content-subtle">—</span>;
  return (
    <span
      title={account.username ? `${account.name} · @${account.username}` : account.name}
      className="flex size-chip items-center justify-center rounded-full bg-info-tint text-small font-medium text-info-strong"
    >
      {account.name.slice(0, 1)}
    </span>
  );
}

function TargetDetails({ row, story }: { row: BoardRow; story: boolean }) {
  const active = ACTIVE_STATES.includes(row.state) && row.state !== 'working';
  const back = row.state === 'failed' || row.state === 'skipped';
  return (
    <div className="border-t border-canvas bg-surface px-4 py-3">
      {active || back ? (
        <div className="mb-3 flex flex-wrap items-center gap-2">
          {active ? (
            <>
              <Button size="sm" variant="primary">
                <Icon name="send" size={12} />
                {story ? 'Отметить сейчас' : 'Сейчас'}
              </Button>
              <Button size="sm" variant="ghost">
                Пропустить
              </Button>
            </>
          ) : (
            <Button size="sm">Вернуть в очередь</Button>
          )}
        </div>
      ) : null}
      {row.history.length === 0 ? (
        <div className="type-small">Ещё не трогали.</div>
      ) : (
        row.history.map((entry) => (
          <div
            key={entry.text}
            className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-canvas py-2 text-body last:border-b-0"
          >
            <span className="shrink-0 text-content-subtle tabular-nums">{entry.at}</span>
            <AccountFace id={row.accountId} />
            <span className={HISTORY_TONE[entry.tone]}>{entry.text}</span>
          </div>
        ))
      )}
    </div>
  );
}

function columns(): ColumnDef<BoardRow>[] {
  return [
    {
      id: 'target',
      header: 'Кто',
      cell: ({ row }) => row.original.target,
      meta: { cellClassName: 'whitespace-nowrap type-body text-action-primary', cardSlot: 'title' },
    },
    {
      id: 'when',
      header: 'Когда',
      cell: ({ row }) => row.original.when,
      meta: { cellClassName: 'whitespace-nowrap type-body text-content-subtle tabular-nums' },
    },
    {
      id: 'account',
      header: 'Аккаунт',
      cell: ({ row }) => <AccountFace id={row.original.accountId} />,
    },
    {
      id: 'status',
      header: 'Статус',
      cell: ({ row }) => (
        <span className="inline-flex items-center gap-2 whitespace-nowrap">
          <Badge tone={ROW[row.original.state].tone}>{ROW[row.original.state].label}</Badge>
          {row.original.reason === undefined ? null : (
            <span className="type-small">{row.original.reason}</span>
          )}
        </span>
      ),
      meta: { cardSlot: 'control' },
    },
    {
      id: 'expander',
      header: () => <span className="sr-only">Подробнее</span>,
      cell: ({ row }) => (
        <button
          type="button"
          aria-label="Подробнее"
          aria-expanded={row.getIsExpanded()}
          onClick={row.getToggleExpandedHandler()}
          className={`-m-3 flex rounded-md p-3 text-content-subtle transition duration-reveal ease-spring hover:text-content-primary ${FOCUS_RING} ${row.getIsExpanded() ? 'rotate-180' : ''}`}
        >
          <Icon name="chevron-down" size={16} />
        </button>
      ),
      meta: { className: 'w-0', cellClassName: 'w-0', cardSlot: 'control' },
    },
  ];
}

function EmptyBody({ icon, title, note, action }: { icon: 'users' | 'clock'; title: string; note?: string; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-2 px-4 py-8 text-center">
      <Icon name={icon} size={20} className="text-content-subtle" />
      <div className="type-body-medium">{title}</div>
      {note === undefined ? null : <div className="type-body text-content-subtle">{note}</div>}
      {action}
    </div>
  );
}

function TargetsBoard({
  rows,
  status,
  story,
  onPace,
  onAddTargets,
}: {
  rows: BoardRow[];
  status: Status;
  story: boolean;
  onPace: () => void;
  onAddTargets: () => void;
}) {
  const active = rows.filter((row) => ACTIVE_STATES.includes(row.state));
  const done = rows.filter((row) => !ACTIVE_STATES.includes(row.state));
  const [tab, setTab] = useState<'active' | 'done'>(active.length === 0 && done.length > 0 ? 'done' : 'active');
  const [showResting, setShowResting] = useState(false);
  const shown = tab === 'active' ? active : done;

  let body;
  if (rows.length === 0) {
    body = (
      <EmptyBody
        icon="users"
        title="Здесь появятся люди из списка"
        note="Добавьте, кого отмечать, в настройках кампании."
        action={
          <Button size="sm" variant="primary" onClick={onAddTargets}>
            Добавить
          </Button>
        }
      />
    );
  } else if (status === 'resting' && tab === 'active' && !showResting) {
    body = (
      <EmptyBody
        icon="clock"
        title={`Продолжим завтра в 09:00 · ждут ${plural(active.length, 'человек', 'человека', 'человек')}`}
        note="Аккаунты выбрали лимит на сегодня."
        action={
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              setShowResting(true);
            }}
          >
            Показать список
          </Button>
        }
      />
    );
  } else if (shown.length === 0) {
    body = (
      <div className="px-4 py-8 text-center type-body text-content-subtle">
        {tab === 'active' ? 'Очередь пуста' : 'Пока никого не отметили'}
      </div>
    );
  } else {
    body = (
      <DataTable
        data={shown}
        columns={columns()}
        getRowId={(row) => row.target}
        renderSubRow={(row) => <TargetDetails row={row.original} story={story} />}
      />
    );
  }

  return (
    <CollapsibleCard
      defaultOpen
      label="Цели"
      headerClassName="border-b border-canvas px-4 py-4"
      bodyClassName="tb-scroll overflow-x-auto"
      title="Цели"
      aside={
        rows.length === 0 ? undefined : (
          <SegmentedControl
            variant="pill"
            value={tab}
            ariaLabel="Какие цели показать"
            options={[
              { value: 'active', label: `В работе · ${String(active.length)}` },
              { value: 'done', label: `Готово · ${String(done.length)}` },
            ]}
            onChange={setTab}
          />
        )
      }
      trailing={
        <IconButton
          size="touch"
          tone="primary"
          title="Темп"
          aria-label="Темп"
          onClick={onPace}
          className="rounded-md sm:size-tile lg:size-icon"
        >
          <Icon name="gear" size={16} />
        </IconButton>
      }
    >
      {body}
    </CollapsibleCard>
  );
}

function CampaignList({
  campaigns,
  selected,
  statusOf,
  onSelect,
  onToggleRun,
  onSettings,
  onCreate,
}: {
  campaigns: (MockCampaign & { meta: string })[];
  selected: string;
  statusOf: (id: string) => Status;
  onSelect: (id: string) => void;
  onToggleRun: (id: string) => void;
  onSettings: (id: string) => void;
  onCreate: (name: string) => void;
}) {
  const [openActions, setOpenActions] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState('');
  const create = () => {
    if (!name.trim()) return;
    onCreate(name.trim());
    setCreating(false);
    setName('');
  };
  return (
    <CollapsibleCard defaultOpen label="Кампании" headerClassName="px-4 py-3" bodyClassName="px-4 pb-4" title="Кампании">
      <div className="flex flex-col gap-2">
        {campaigns.map((campaign) => {
          const status = statusOf(campaign.id);
          const going = status === 'running' || status === 'resting';
          return (
            <SelectableCard
              key={campaign.id}
              surfaceId={`mt-camp-${campaign.id}`}
              name={campaign.name}
              meta={campaign.meta}
              status={<Badge tone={STATUS[status].tone}>{STATUS[status].label}</Badge>}
              selected={campaign.id === selected}
              actionsOpen={openActions === campaign.id}
              actionsLabel="Действия с кампанией"
              onSelect={() => {
                onSelect(campaign.id);
              }}
              onToggleActions={() => {
                setOpenActions((value) => (value === campaign.id ? null : campaign.id));
              }}
              actions={
                <>
                  <IconButton
                    size="md"
                    tone="neutral"
                    aria-label={going ? 'Пауза' : 'Запустить'}
                    title={going ? 'Пауза' : 'Запустить'}
                    disabled={status === 'draft'}
                    className={going ? 'text-warning-deep hover:bg-warning-tint' : 'text-success-deep hover:bg-success-tint'}
                    onClick={() => {
                      onToggleRun(campaign.id);
                    }}
                  >
                    <Icon name={going ? 'pause' : 'play'} size={16} />
                  </IconButton>
                  <IconButton
                    size="md"
                    tone="primary"
                    aria-label="Настройки"
                    title="Настройки"
                    onClick={() => {
                      onSettings(campaign.id);
                    }}
                  >
                    <Icon name="pencil" size={16} />
                  </IconButton>
                  <IconButton size="md" tone="danger" aria-label="Удалить" title="Удалить">
                    <Icon name="trash" size={16} />
                  </IconButton>
                </>
              }
            />
          );
        })}
      </div>
      {creating ? (
        <div className="mt-2 flex items-center gap-2">
          <Input
            size="sm"
            autoFocus
            value={name}
            onChange={(event) => {
              setName(event.target.value);
            }}
            onKeyDown={(event) => {
              if (event.key === 'Enter') create();
              if (event.key === 'Escape') setCreating(false);
            }}
            placeholder="Название"
            aria-label="Название кампании"
            className="min-w-0 flex-1 border-action-primary"
          />
          <Button variant="primary" size="sm" disabled={!name.trim()} onClick={create}>
            Создать
          </Button>
          <IconButton
            size="sm"
            aria-label="Отмена"
            onClick={() => {
              setCreating(false);
            }}
          >
            <Icon name="close" size={14} />
          </IconButton>
        </div>
      ) : (
        <Button
          variant="dashed"
          fullWidth
          className="mt-2 font-medium"
          onClick={() => {
            setCreating(true);
          }}
        >
          + Новая кампания
        </Button>
      )}
    </CollapsibleCard>
  );
}

export function VariantD({ draft, patch }: { draft: Draft; patch: Patch; running: boolean; onToggleRun: () => void }) {
  const [campaigns, setCampaigns] = useState(CAMPAIGNS);
  // Statuses the operator changed; the live one starts as draft/ready from its settings.
  const [statuses, setStatuses] = useState<Record<string, Status>>({});
  const [selected, setSelected] = useState(LIVE_ID);
  const [settingsFor, setSettingsFor] = useState<string | null>(null);
  const [paceOpen, setPaceOpen] = useState(false);

  const draftOf = (campaign: MockCampaign): Draft =>
    campaign.id === LIVE_ID ? draft : { ...draft, mode: campaign.mode };
  const statusOf = (id: string): Status => {
    const set = statuses[id];
    if (set !== undefined) return set;
    const campaign = campaigns.find((c) => c.id === id);
    if (campaign === undefined) return 'draft';
    if (campaign.id !== LIVE_ID) return campaign.status;
    return blockers(draft).length > 0 ? 'draft' : 'ready';
  };
  const toggle = (id: string) => {
    const status = statusOf(id);
    const next: Status =
      status === 'running' || status === 'resting' ? 'paused' : status === 'draft' ? 'draft' : 'running';
    setStatuses((map) => ({ ...map, [id]: next }));
  };

  const current = campaigns.find((c) => c.id === selected) ?? campaigns[0];
  if (current === undefined) return null;
  const currentDraft = draftOf(current);
  const status = statusOf(current.id);
  const story = current.mode === 'stories';
  const rows = boardRows(current, status, currentDraft);
  const view = pipeline(current, status, currentDraft, rows);
  const { free } = estimate(currentDraft);
  const listed = campaigns.map((c) => {
    const mode = c.id === LIVE_ID ? draft.mode : c.mode;
    const size = targetsOf(c, draft).length;
    return { ...c, mode, meta: `${MODE_SHORT[mode]} · ${plural(size, 'цель', 'цели', 'целей')}` };
  });
  const settingsCampaign = campaigns.find((c) => c.id === settingsFor);

  return (
    <div className="tb-fadeup mx-auto max-w-shell pb-16">
      <h1 className="m-0 mb-6 type-h1">Масстегинг</h1>
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start">
        <div className="flex flex-col gap-3 lg:w-sidebar lg:shrink-0">
          <CampaignList
            campaigns={listed}
            selected={current.id}
            statusOf={statusOf}
            onSelect={setSelected}
            onToggleRun={(id) => {
              setSelected(id);
              toggle(id);
            }}
            onSettings={(id) => {
              setSelected(id);
              setSettingsFor(id);
            }}
            onCreate={(name) => {
              const id = `n${String(campaigns.length + 1)}`;
              setCampaigns((list) => [...list, { id, name, mode: 'stories', status: 'draft', size: 0 }]);
              setSelected(id);
              setSettingsFor(id);
            }}
          />
          <HowItWorksCard title="Как это работает" steps={HOW_STEPS} />
        </div>

        <div className="flex min-w-0 flex-1 flex-col gap-4">
          <PipelineCard
            title={
              <>
                Конвейер<span className="text-action-primary"> — {current.name}</span>
              </>
            }
            status={<Badge tone={STATUS[status].tone}>{STATUS[status].label}</Badge>}
            running={view.action.kind === 'pause'}
            toggleDisabled={view.action.disabled}
            startLabel={view.action.label}
            stopLabel={view.action.label}
            onToggle={() => {
              toggle(current.id);
            }}
            steps={view.steps}
            narrow="list"
            notice={view.notice}
            stats={view.stats}
          >
            <div className="flex flex-col gap-3">
              {view.started ? (
                <ProgressBar value={view.progress.done} max={Math.max(1, view.progress.total)} className="w-full" />
              ) : null}
              <div className="flex flex-wrap items-center gap-2">
                <Badge>{MODE_SHORT[current.mode]}</Badge>
                {story && free > 0 && status !== 'done' ? (
                  <span className="flex items-center gap-1">
                    <Badge tone="warning">без Premium: {free}</Badge>
                    <HelpHint text="Такие аккаунты выкладывают 1 сторис в день." example="С Premium — до 100." />
                  </span>
                ) : null}
                <Button
                  variant="ghost"
                  size="sm"
                  className="ml-auto"
                  onClick={() => {
                    setSettingsFor(current.id);
                  }}
                >
                  <Icon name="pencil" size={14} />
                  Настройки
                </Button>
              </div>
            </div>
          </PipelineCard>
          <TargetsBoard
            key={`${current.id}-${status}`}
            rows={rows}
            status={status}
            story={story}
            onPace={() => {
              setPaceOpen(true);
            }}
            onAddTargets={() => {
              setSettingsFor(current.id);
            }}
          />
          <LogTerminal
            title="Журнал"
            logLines={logLines(current.mode, status, currentDraft.accountIds)}
            onClear={() => undefined}
          />
        </div>
      </div>

      {settingsCampaign !== undefined ? (
        <SettingsDialogD
          name={settingsCampaign.name}
          initial={draftOf(settingsCampaign)}
          locked={statusOf(settingsCampaign.id) === 'running' || statusOf(settingsCampaign.id) === 'resting'}
          onClose={() => {
            setSettingsFor(null);
          }}
          onSave={(next) => {
            if (settingsCampaign.id === LIVE_ID) patch(next);
            else setCampaigns((list) => list.map((c) => (c.id === settingsCampaign.id ? { ...c, mode: next.mode } : c)));
          }}
        />
      ) : null}
      {paceOpen ? (
        <PaceDialog
          initial={currentDraft}
          onClose={() => {
            setPaceOpen(false);
          }}
          onSave={(next) => {
            patch(next);
          }}
        />
      ) : null}
    </div>
  );
}
