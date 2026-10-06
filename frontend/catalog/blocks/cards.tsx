// Блоки карточек: шапка, плитки чисел, полоса, линия этапов и карточка конвейера.
import {
  Badge,
  CardHeader,
  Icon,
  IconButton,
  PipelineCard,
  ProgressBar,
  StatGrid,
  Stepper,
  Switch,
} from '@/shared/ui';
import type { Stat, StepperStep } from '@/shared/ui';

import { InCard } from './frames';
import { type BlockDemo, noop } from './types';

const STATS: Stat[] = [
  { label: 'Кампаний', value: 2 },
  { label: 'Каналов', value: 4, tone: 'primary' },
  { label: 'Аккаунтов', value: 2 },
  { label: 'Комментариев', value: 14, tone: 'success' },
  { label: 'Удалено', value: 1, tone: 'danger' },
  { label: 'Ошибок', value: 2, tone: 'danger' },
];

const RUNTIME: StepperStep[] = [
  { id: 'listen', label: 'Слушатель', state: 'done' },
  { id: 'detect', label: 'Новый пост', state: 'done' },
  { id: 'filter', label: 'Фильтр', state: 'current' },
  { id: 'generate', label: 'Генерация', state: 'upcoming' },
  { id: 'solve', label: 'Капча', state: 'upcoming' },
  { id: 'comment', label: 'Комментарий', state: 'upcoming' },
];

const LAUNCH: StepperStep[] = [
  { id: 'scenario', label: 'Сценарий', caption: 'утверждён', state: 'done' },
  { id: 'accounts', label: 'Аккаунты', caption: '1 из 2 ролей', state: 'upcoming' },
  { id: 'targets', label: 'Цели', caption: '2 цели', state: 'done' },
  { id: 'intro', label: 'Вступление', caption: 'пауза 40–180 с', state: 'upcoming' },
];

export const CARD_BLOCKS: BlockDemo[] = [
  {
    id: 'card-header',
    name: 'CardHeader',
    variants: [
      {
        label: 'с плиткой и переключателем',
        node: (
          <InCard>
            <CardHeader
              icon={<Icon name="shield-check" size={16} />}
              title="Решение капчи"
              subtitle="Авто-проход бот-чека"
            >
              <Switch checked onChange={noop} label="Решение капчи" />
            </CardHeader>
          </InCard>
        ),
      },
      {
        label: 'с плашкой и действием',
        node: (
          <InCard>
            <CardHeader title="Доска работ" badge={<Badge tone="info">2 аккаунта</Badge>}>
              <IconButton size="md" tone="primary" aria-label="Настроить">
                <Icon name="gear" size={16} />
              </IconButton>
            </CardHeader>
          </InCard>
        ),
      },
      {
        label: 'с точкой',
        node: (
          <InCard>
            <CardHeader
              dot="live"
              title="Переписка аккаунтов"
              badge={<Badge tone="success">7 пар</Badge>}
            />
          </InCard>
        ),
      },
      {
        label: 'success',
        node: (
          <InCard>
            <CardHeader
              icon={<Icon name="check" size={16} />}
              tone="success"
              title="Прогреты"
              badge={<Badge tone="success">1</Badge>}
            />
          </InCard>
        ),
      },
    ],
  },
  {
    id: 'stat-grid',
    name: 'StatGrid',
    variants: [
      { label: 'четыре', node: <StatGrid stats={STATS.slice(2)} /> },
      {
        label: 'три',
        node: (
          <StatGrid
            stats={[
              { label: 'в прогреве', value: 3, tone: 'primary' },
              { label: 'готовы', value: 1 },
              { label: 'ошибки', value: 0, tone: 'danger' },
            ]}
          />
        ),
      },
      {
        label: 'строка',
        node: (
          <StatGrid
            stats={[
              { label: 'Реплик', value: 4 },
              { label: 'Диалог в цели', value: '3:45' },
            ]}
          />
        ),
      },
    ],
  },
  {
    id: 'progress-bar',
    name: 'ProgressBar',
    variants: [
      {
        label: 'primary',
        node: (
          <InCard>
            <ProgressBar label="Прогресс" value={34} max={120} />
          </InCard>
        ),
      },
      {
        label: 'success',
        node: (
          <InCard>
            <ProgressBar tone="success" value={1} max={1} />
          </InCard>
        ),
      },
      {
        label: 'warning',
        node: (
          <InCard>
            <ProgressBar tone="warning" value={17} max={20} />
          </InCard>
        ),
      },
      {
        label: 'danger',
        node: (
          <InCard>
            <ProgressBar tone="danger" value={5} max={5} />
          </InCard>
        ),
      },
      {
        label: 'indeterminate',
        node: (
          <InCard>
            <ProgressBar label="Поиск" value={0} max={0} indeterminate />
          </InCard>
        ),
      },
      {
        label: 'segments',
        node: (
          <InCard>
            <ProgressBar segments={42} value={8} max={15} />
          </InCard>
        ),
      },
    ],
  },
  {
    id: 'stepper',
    name: 'Stepper',
    variants: [
      { label: 'конвейер', node: <Stepper steps={RUNTIME} narrow="current" /> },
      { label: 'с подписями', node: <Stepper steps={LAUNCH} narrow="list" /> },
      {
        label: 'шаги мастера',
        node: (
          <Stepper
            numbered
            steps={[
              { id: '1', state: 'done' },
              { id: '2', state: 'current' },
              { id: '3', state: 'upcoming' },
              { id: '4', state: 'upcoming' },
            ]}
          />
        ),
      },
    ],
  },
  {
    id: 'pipeline-card',
    name: 'PipelineCard',
    variants: [
      {
        label: 'идёт',
        node: (
          <PipelineCard
            title="Конвейер обработки постов"
            status={
              <Badge size="sm" tone="success">
                активен
              </Badge>
            }
            running
            startLabel="Запустить"
            stopLabel="Остановить"
            onToggle={noop}
            steps={RUNTIME}
            notice={{ tone: 'info', text: 'Слушаю новые посты и комментирую' }}
            stats={STATS.slice(0, 4)}
          />
        ),
      },
      {
        label: 'готов к запуску',
        node: (
          <PipelineCard
            title="Конвейер"
            status={<Badge dot>Не запущена</Badge>}
            running={false}
            startLabel="Запустить"
            stopLabel="Остановить"
            onToggle={noop}
            steps={LAUNCH}
            narrow="list"
            notice={{ tone: 'success', text: 'Готово к запуску' }}
            stats={[
              { label: 'Аккаунтов', value: 2 },
              { label: 'Целей', value: 2 },
              { label: 'Диалог в цели', value: '3:45' },
            ]}
          >
            <ProgressBar label="Прогресс прогона" value={34} max={120} />
          </PipelineCard>
        ),
      },
    ],
  },
];
