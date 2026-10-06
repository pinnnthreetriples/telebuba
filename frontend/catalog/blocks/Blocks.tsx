// Блоки — составные куски экрана, которые повторяются на разных страницах, каждый одним
// компонентом из `src/shared/ui`. Этот список — единственное место, где описаны их
// образцы: его рендерит раздел каталога (визуальный гейт) и он же, через
// `react-dom/server`, становится `docs/blocks.html` (`scripts/blocks-doc.mjs`). Образец,
// перерисованный в HTML руками, расходился бы с компонентом; этот рисует сам компонент.
import type { ReactNode } from 'react';

import {
  Badge,
  Button,
  CollapsibleCard,
  EmptyState,
  HowItWorksCard,
  Icon,
  Input,
  ModalFooter,
  ModalHeader,
  Notice,
  SectionLabel,
  SegmentedControl,
  SettingRow,
  Switch,
} from '@/shared/ui';

import { InCard, InDialog } from './frames';
import { LIBRARY_BLOCKS } from './library';

export type BlockDemo = {
  id: string;
  // Имя компонента: по нему генератор находит файл и все места, где блок стоит.
  name: string;
  // Блок библиотеки: в `shared/ui` есть, на экранах ещё нет (`library.tsx`).
  library?: true;
  variants: { label: string; node: ReactNode }[];
};

const noop = () => undefined;

export const BLOCKS: BlockDemo[] = [
  {
    id: 'modal-header',
    name: 'ModalHeader',
    variants: [
      {
        label: 'с иконкой',
        node: (
          <InDialog>
            <ModalHeader
              title="Новая кампания"
              subtitle="Название, промпт и каналы"
              icon={<Icon name="plus" size={18} />}
            />
          </InDialog>
        ),
      },
      {
        label: 'с хвостом',
        node: (
          <InDialog>
            <ModalHeader title="Крипта" subtitle="Настройки кампании">
              <div className="flex-1" />
              <Badge tone="warning">не сохранено</Badge>
            </ModalHeader>
          </InDialog>
        ),
      },
      {
        label: 'только заголовок',
        node: (
          <InDialog>
            <ModalHeader title="История комментариев" />
          </InDialog>
        ),
      },
    ],
  },
  {
    id: 'modal-footer',
    name: 'ModalFooter',
    variants: [
      {
        label: 'кнопки',
        node: (
          <InDialog>
            <ModalFooter>
              <Button size="sm">Отмена</Button>
              <Button size="sm" variant="primary">
                Сохранить
              </Button>
            </ModalFooter>
          </InDialog>
        ),
      },
      {
        label: 'итог слева',
        node: (
          <InDialog>
            <ModalFooter>
              <span className="mr-auto type-small tabular-nums">Итого 4:30</span>
              <Button size="sm">Перегенерировать</Button>
              <Button size="sm" variant="primary">
                Одобрить
              </Button>
            </ModalFooter>
          </InDialog>
        ),
      },
    ],
  },
  {
    id: 'setting-row',
    name: 'SettingRow',
    variants: [
      {
        label: 'группа строк',
        node: (
          <InCard>
            <SectionLabel title="Запуск" caption="как проходит прогон" />
            <SettingRow first label="Обход целей">
              <SegmentedControl
                value="order"
                onChange={noop}
                ariaLabel="Обход целей"
                options={[
                  { value: 'order', label: 'Подряд' },
                  { value: 'random', label: 'Вразброс' },
                ]}
              />
            </SettingRow>
            <SettingRow label="Пауза между сообщениями" hint="Секунды, от 5 до 600">
              <Input size="sm" className="w-number tabular-nums" value="30" readOnly />
            </SettingRow>
            <SettingRow label="Автоответчик" hint="Отвечать на реплики в чате">
              <Switch checked onChange={noop} label="Автоответчик" />
            </SettingRow>
          </InCard>
        ),
      },
    ],
  },
  {
    id: 'section-label',
    name: 'SectionLabel',
    variants: [
      { label: 'с уточнением', node: <SectionLabel title="Цели" caption="3 канала" /> },
      { label: 'без уточнения', node: <SectionLabel title="Фильтры" /> },
    ],
  },
  {
    id: 'empty-state',
    name: 'EmptyState',
    variants: [
      {
        label: 'в списке',
        node: (
          <InCard>
            <EmptyState>Кампаний пока нет</EmptyState>
          </InCard>
        ),
      },
      {
        label: 'boxed',
        node: (
          <EmptyState boxed size="md">
            В канале ещё нет постов
          </EmptyState>
        ),
      },
      {
        label: 'danger',
        node: (
          <InCard>
            <EmptyState tone="danger">Не удалось загрузить историю</EmptyState>
          </InCard>
        ),
      },
    ],
  },
  {
    id: 'collapsible-card',
    name: 'CollapsibleCard',
    variants: [
      {
        label: 'раскрыта',
        node: (
          <CollapsibleCard
            defaultOpen
            label="Каналы кампании"
            header={<span className="type-h3">Каналы кампании</span>}
            trailing={<Badge size="xs">3</Badge>}
          >
            <div className="flex flex-col gap-1 type-body text-content-muted">
              <span>@crypto_news</span>
              <span>@defi_daily</span>
              <span>@ton_chat</span>
            </div>
          </CollapsibleCard>
        ),
      },
      {
        label: 'свёрнута',
        node: (
          <CollapsibleCard label="Журнал" header={<span className="type-h3">Журнал</span>}>
            <span />
          </CollapsibleCard>
        ),
      },
    ],
  },
  {
    id: 'how-it-works',
    name: 'HowItWorksCard',
    variants: [
      {
        label: 'раскрыта',
        node: (
          <HowItWorksCard
            defaultOpen
            title="Как это работает"
            steps={[
              'Создайте кампанию и добавьте каналы',
              'Назначьте прогретые аккаунты',
              'Включите слушателя',
              'Комментарии появятся в ленте',
            ]}
          />
        ),
      },
    ],
  },
  {
    id: 'notice',
    name: 'Notice',
    variants: [
      { label: 'info', node: <Notice tone="info">Поиск каналов идёт в фоне</Notice> },
      { label: 'success', node: <Notice tone="success">Прокси сохранён</Notice> },
      { label: 'warning', node: <Notice tone="warning">Два аккаунта без прокси</Notice> },
      { label: 'danger', node: <Notice tone="danger">Сессия больше не действует</Notice> },
    ],
  },
];

// Библиотека — в конце и отдельным файлом: у этих блоков ещё нет экрана (см. `library.tsx`).
BLOCKS.push(...LIBRARY_BLOCKS);
