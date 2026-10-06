// Блоки из Storybook: составные куски, у которых до сих пор был только рассказ, а не
// место на странице блоков. Одиночных контролов (Button, Input…) здесь нет — они на
// странице дизайн-системы.
import type { ColumnDef } from '@tanstack/react-table';

import {
  Badge,
  ChipAddButton,
  DataTable,
  Icon,
  IconButton,
  InlineChipEditor,
  NumberedStep,
  SelectableCard,
  TerminalPane,
} from '@/shared/ui';
import { DashedAdd, DashedEmptyAction } from '@/widgets/account-edit/ui/_shared';
import { RetryNotice } from '@/widgets/account-edit/ui/RetryNotice';

import { InCard } from './frames';
import { type BlockDemo, noop } from './types';

type Row = { name: string; phone: string; state: string };

const ROWS: Row[] = [
  { name: 'Иван Петров', phone: '+7 900 111-22-33', state: 'Прогрет' },
  { name: 'Мария Смирнова', phone: '+7 900 444-55-66', state: 'В прогреве' },
];

const COLUMNS: ColumnDef<Row>[] = [
  { id: 'name', header: 'Аккаунт', accessorKey: 'name' },
  { id: 'phone', header: 'Телефон', accessorKey: 'phone' },
  {
    id: 'state',
    header: 'Состояние',
    cell: ({ row }) => <Badge dot>{row.original.state}</Badge>,
  },
];

const ACTIVE = (
  <span className="inline-flex items-center gap-1 type-small-medium text-success-deep">
    <span className="size-dot rounded-full bg-current" />
    Активна
  </span>
);

function campaign(selected: boolean) {
  return (
    <SelectableCard
      surfaceId={`block-campaign-${String(selected)}`}
      name="Крипта"
      meta="4 канала · 3 аккаунта"
      status={ACTIVE}
      selected={selected}
      actionsOpen={false}
      actionsLabel="Действия с кампанией"
      onSelect={noop}
      onToggleActions={noop}
      actions={
        <>
          <IconButton size="md" tone="primary" aria-label="Изменить кампанию">
            <Icon name="pencil" size={16} />
          </IconButton>
          <IconButton size="md" tone="danger" aria-label="Удалить кампанию">
            <Icon name="trash" size={16} />
          </IconButton>
        </>
      }
    />
  );
}

export const PATTERN_BLOCKS: BlockDemo[] = [
  {
    id: 'selectable-card',
    name: 'SelectableCard',
    variants: [
      { label: 'обычная', node: campaign(false) },
      { label: 'выбрана', node: campaign(true) },
    ],
  },
  {
    id: 'data-table',
    name: 'DataTable',
    variants: [
      {
        label: 'таблица',
        node: <DataTable data={ROWS} columns={COLUMNS} />,
      },
    ],
  },
  {
    id: 'terminal-pane',
    name: 'TerminalPane',
    variants: [
      {
        label: 'лента',
        node: (
          <TerminalPane>
            <div className="flex gap-2">
              <span className="text-term-dim">12:50:19</span>
              <span className="text-term-link">moscowmap</span>
              <span className="text-term-success">Новый пост</span>
            </div>
            <div className="flex gap-2">
              <span className="text-term-dim">12:27:07</span>
              <span className="text-term-link">shot_shot</span>
              <span className="text-term-error">Ошибка публикации</span>
            </div>
          </TerminalPane>
        ),
      },
    ],
  },
  {
    id: 'inline-chip-editor',
    name: 'InlineChipEditor',
    variants: [
      {
        label: 'ввод',
        node: (
          <InlineChipEditor
            value="@crypto_daily"
            onChange={noop}
            onConfirm={noop}
            onCancel={noop}
            placeholder="@канал"
            inputLabel="Канал"
            confirmLabel="Добавить"
            cancelLabel="Отмена"
          />
        ),
      },
      { label: 'свёрнут', node: <ChipAddButton onClick={noop}>Канал</ChipAddButton> },
    ],
  },
  {
    id: 'numbered-step',
    name: 'NumberedStep',
    variants: [
      {
        label: 'шаги',
        node: (
          <div className="flex flex-col gap-3">
            <NumberedStep number={1}>Выберите аккаунты</NumberedStep>
            <NumberedStep number={2}>Настройте каналы</NumberedStep>
          </div>
        ),
      },
    ],
  },
  {
    id: 'retry-notice',
    name: 'RetryNotice',
    variants: [
      {
        label: 'ошибка',
        node: (
          <RetryNotice
            message="Не удалось загрузить настройки аккаунта."
            label="Повторить"
            onRetry={noop}
          />
        ),
      },
    ],
  },
  {
    id: 'dashed-empty-action',
    name: 'DashedEmptyAction',
    variants: [
      {
        label: 'пустой список',
        node: (
          <InCard>
            <DashedEmptyAction
              idleLabel="У аккаунта пока нет каналов"
              actionLabel="Создать канал"
              onClick={noop}
            />
          </InCard>
        ),
      },
    ],
  },
  {
    id: 'dashed-add',
    name: 'DashedAdd',
    variants: [
      {
        label: 'фото',
        node: (
          <div className="w-col">
            <DashedAdd ratio="1" label="Загрузить" onClick={noop} />
          </div>
        ),
      },
      {
        label: 'сторис',
        node: (
          <div className="w-col">
            <DashedAdd ratio="9 / 16" label="Добавить сторис" onClick={noop} />
          </div>
        ),
      },
    ],
  },
];
