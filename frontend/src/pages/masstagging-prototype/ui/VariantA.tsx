// PROTOTYPE — throwaway. Variant A: the reference layout in Telebuba's skin.
// Dual-list account picker on top, then two columns of collapsible cards; priming
// is a switch inside «Настройки сторис» and story-only fields go grey.
import { Badge, Button, CollapsibleCard, Icon, IconButton, Notice } from '@/shared/ui';

import { ACCOUNTS, type Draft, estimate, type Patch } from './model';
import {
  AccountFilterBar,
  AccountLine,
  CreativeFields,
  LaunchPanel,
  PacingRows,
  PremiumNotice,
  StoryOptions,
  TargetsEditor,
  useAccountFilters,
  VolumeFields,
} from './parts';

export const NAME = 'Как в референсе';

function DualList({ draft, patch }: { draft: Draft; patch: Patch }) {
  const { filters, setFilters, visible } = useAccountFilters();
  const available = visible.filter((a) => !draft.accountIds.includes(a.id));
  const chosen = ACCOUNTS.filter((a) => draft.accountIds.includes(a.id));
  return (
    <div className="flex flex-col gap-3">
      <div className="grid gap-4 md:grid-cols-2">
        <div className="flex flex-col gap-3 rounded-lg border border-line p-3">
          <div className="flex items-center justify-between">
            <span className="type-body-medium">Доступные аккаунты</span>
            <Badge>{available.length}</Badge>
          </div>
          <AccountFilterBar filters={filters} setFilters={setFilters} />
          <Button
            size="sm"
            disabled={available.length === 0}
            onClick={() => {
              patch({ accountIds: [...draft.accountIds, ...available.map((a) => a.id)] });
            }}
          >
            Добавить все
            <Icon name="chevron-right" size={14} />
          </Button>
          <div className="flex flex-col">
            {available.map((account) => (
              <div key={account.id} className="flex items-center border-t border-canvas py-2">
                <AccountLine
                  account={account}
                  mode={draft.mode}
                  trailing={
                    <IconButton
                      size="sm"
                      tone="primary"
                      aria-label={`Добавить ${account.name}`}
                      onClick={() => {
                        patch({ accountIds: [...draft.accountIds, account.id] });
                      }}
                    >
                      <Icon name="chevron-right" size={14} />
                    </IconButton>
                  }
                />
              </div>
            ))}
          </div>
        </div>
        <div className="flex flex-col gap-3 rounded-lg border border-line p-3">
          <div className="flex items-center justify-between">
            <span className="type-body-medium">Выбрано для отметок</span>
            <Badge tone="info">{chosen.length}</Badge>
          </div>
          <Button
            size="sm"
            variant="danger"
            disabled={chosen.length === 0}
            onClick={() => {
              patch({ accountIds: [] });
            }}
          >
            <Icon name="chevron-left" size={14} />
            Убрать все
          </Button>
          <div className="flex flex-col">
            {chosen.map((account) => (
              <div key={account.id} className="flex items-center border-t border-canvas py-2">
                <AccountLine
                  account={account}
                  mode={draft.mode}
                  trailing={
                    <IconButton
                      size="sm"
                      tone="danger"
                      aria-label={`Убрать ${account.name}`}
                      onClick={() => {
                        patch({ accountIds: draft.accountIds.filter((id) => id !== account.id) });
                      }}
                    >
                      <Icon name="chevron-left" size={14} />
                    </IconButton>
                  }
                />
              </div>
            ))}
          </div>
        </div>
      </div>
      <PremiumNotice draft={draft} />
    </div>
  );
}

const PRIMING_OFF =
  'Режим «Только прайминг»: сторис не публикуются, поэтому пул медиа, подпись и ссылка не используются.';

export function VariantA({
  draft,
  patch,
  running,
  onToggleRun,
}: {
  draft: Draft;
  patch: Patch;
  running: boolean;
  onToggleRun: () => void;
}) {
  const priming = draft.mode === 'priming';
  const { perDay } = estimate(draft);
  return (
    <div className="flex flex-col gap-4 pb-16">
      <div>
        <h1 className="m-0 mb-1 type-h1">Масстегинг</h1>
        <p className="m-0 type-small">
          Отмечаем пользователей в сторис по списку целей — пуш «вас упомянули в истории» читается как
          соцвзаимодействие, а не реклама.
        </p>
      </div>

      <CollapsibleCard
        defaultOpen
        label="Аккаунты"
        title="Аккаунты" icon={<Icon name="users" size={16} />} badge={<Badge tone="info">{draft.accountIds.length} выбрано</Badge>}
      >
        <DualList draft={draft} patch={patch} />
      </CollapsibleCard>

      <div className="grid items-start gap-4 lg:grid-cols-2">
        <div className="flex flex-col gap-4">
          <CollapsibleCard
            defaultOpen
            label="Цели"
            title="Цели (кого отмечаем)" icon={<Icon name="user-plus" size={16} />} badge={<Badge>{draft.targets.length}</Badge>}
          >
            <TargetsEditor draft={draft} patch={patch} />
          </CollapsibleCard>
          <CollapsibleCard
            defaultOpen
            label="Пул медиа"
            title="Пул медиа" icon={<Icon name="video" size={16} />} badge={
                  priming ? (
                    <Badge tone="warning">не используется</Badge>
                  ) : (
                    <Badge>{draft.media.length} файлов</Badge>
                  )
                }
          >
            {priming ? (
              <Notice tone="info" className="mb-3">
                {PRIMING_OFF}
              </Notice>
            ) : null}
            <CreativeFields draft={draft} patch={patch} disabled={priming} />
          </CollapsibleCard>
        </div>

        <div className="flex flex-col gap-4">
          <CollapsibleCard
            defaultOpen
            label="Задержки"
            title="Задержки и лимиты" icon={<Icon name="clock" size={16} />}
          >
            {priming ? (
              <Notice tone="info" className="mb-2">
                Режим «Только прайминг»: настройки сторис не используются. Пауза перед диалогом и
                флуд-лимиты — работают.
              </Notice>
            ) : null}
            <PacingRows draft={draft} patch={patch} inactive="dim" />
          </CollapsibleCard>
          <CollapsibleCard
            defaultOpen
            label="Темп и объём"
            title="Темп и объём" icon={<Icon name="chart" size={16} />}
          >
            <VolumeFields draft={draft} patch={patch} />
          </CollapsibleCard>
          <CollapsibleCard
            defaultOpen
            label="Настройки сторис"
            title="Настройки сторис" icon={<Icon name="eye" size={16} />}
          >
            <StoryOptions draft={draft} patch={patch} withPrimingSwitch />
          </CollapsibleCard>
          <CollapsibleCard
            defaultOpen
            label="Запуск"
            title="Запуск" icon={<Icon name="play" size={16} />} badge={<Badge>{perDay} в сутки</Badge>}
          >
            <LaunchPanel draft={draft} running={running} onToggle={onToggleRun} />
          </CollapsibleCard>
        </div>
      </div>
    </div>
  );
}
