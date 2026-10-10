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
  SectionHead,
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
    <div className="flex flex-col gap-md">
      <div className="grid gap-lg md:grid-cols-2">
        <div className="flex flex-col gap-md rounded-lg border border-line p-md">
          <div className="flex items-center justify-between">
            <span className="type-item-title">Доступные аккаунты</span>
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
              <div key={account.id} className="flex items-center border-t border-line-row py-sm">
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
        <div className="flex flex-col gap-md rounded-lg border border-line p-md">
          <div className="flex items-center justify-between">
            <span className="type-item-title">Выбрано для отметок</span>
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
              <div key={account.id} className="flex items-center border-t border-line-row py-sm">
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
    <div className="flex flex-col gap-lg pb-empty">
      <div>
        <h1 className="m-0 mb-xs type-page-title">Масстегинг</h1>
        <p className="m-0 type-caption">
          Отмечаем пользователей в сторис по списку целей — пуш «вас упомянули в истории» читается как
          соцвзаимодействие, а не реклама.
        </p>
      </div>

      <CollapsibleCard
        defaultOpen
        label="Аккаунты"
        header={
          <SectionHead
            icon="users"
            title="Аккаунты"
            badge={<Badge tone="info">{draft.accountIds.length} выбрано</Badge>}
          />
        }
      >
        <DualList draft={draft} patch={patch} />
      </CollapsibleCard>

      <div className="grid items-start gap-lg lg:grid-cols-2">
        <div className="flex flex-col gap-lg">
          <CollapsibleCard
            defaultOpen
            label="Цели"
            header={
              <SectionHead
                icon="user-plus"
                title="Цели (кого отмечаем)"
                badge={<Badge>{draft.targets.length}</Badge>}
              />
            }
          >
            <TargetsEditor draft={draft} patch={patch} />
          </CollapsibleCard>
          <CollapsibleCard
            defaultOpen
            label="Пул медиа"
            header={
              <SectionHead
                icon="video"
                title="Пул медиа"
                badge={
                  priming ? (
                    <Badge tone="warning">не используется</Badge>
                  ) : (
                    <Badge>{draft.media.length} файлов</Badge>
                  )
                }
              />
            }
          >
            {priming ? (
              <Notice tone="info" className="mb-md">
                {PRIMING_OFF}
              </Notice>
            ) : null}
            <CreativeFields draft={draft} patch={patch} disabled={priming} />
          </CollapsibleCard>
        </div>

        <div className="flex flex-col gap-lg">
          <CollapsibleCard
            defaultOpen
            label="Задержки"
            header={<SectionHead icon="clock" title="Задержки и лимиты" />}
          >
            {priming ? (
              <Notice tone="info" className="mb-sm">
                Режим «Только прайминг»: настройки сторис не используются. Пауза перед диалогом и
                флуд-лимиты — работают.
              </Notice>
            ) : null}
            <PacingRows draft={draft} patch={patch} inactive="dim" />
          </CollapsibleCard>
          <CollapsibleCard
            defaultOpen
            label="Темп и объём"
            header={<SectionHead icon="chart" title="Темп и объём" />}
          >
            <VolumeFields draft={draft} patch={patch} />
          </CollapsibleCard>
          <CollapsibleCard
            defaultOpen
            label="Настройки сторис"
            header={<SectionHead icon="eye" title="Настройки сторис" />}
          >
            <StoryOptions draft={draft} patch={patch} withPrimingSwitch />
          </CollapsibleCard>
          <CollapsibleCard
            defaultOpen
            label="Запуск"
            header={<SectionHead icon="play" title="Запуск" badge={<Badge>{perDay} в сутки</Badge>} />}
          >
            <LaunchPanel draft={draft} running={running} onToggle={onToggleRun} />
          </CollapsibleCard>
        </div>
      </div>
    </div>
  );
}
