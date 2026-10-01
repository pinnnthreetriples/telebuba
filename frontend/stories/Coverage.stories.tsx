import type { Meta, StoryObj } from '@storybook/react-vite';
import { Card, SectionStack } from '../src/shared/ui';

const cases = [
  ...(
    ['accounts', 'warming', 'neurocomment', 'neuroshilling', 'logs', 'settings', 'login'] as const
  ).map(
    (page) =>
      [
        page,
        `design-system-screens--${page}`,
        'populated / empty / loading / error; locale ru / en (state влияет на запросы, Login остаётся локальной формой)',
        'layout pageFrame / sectionGap; card / controls / table / statTile / search',
      ] as const,
  ),
  [
    'CampaignDetails',
    'design-system-patterns-campaign-dialogs--campaign-details',
    'default / empty; длинная таблица и название',
    'modal shell / header / body / footer; table / badge',
  ],
  [
    'CampaignSettings',
    'design-system-patterns-campaign-dialogs--campaign-settings',
    'default / busy / conflict; локальный черновик',
    'modal / form labelGap / controls / segmented',
  ],
  [
    'AdvancedLimits',
    'design-system-patterns-campaign-dialogs--advanced-limits',
    'default / empty reserve / busy (live)',
    'modal / controls / switch / helpHint / badge',
  ],
  [
    'Media',
    'design-system-patterns-campaign-dialogs--media',
    'default / empty steps; локальное применение и отмена',
    'modal / controls / select',
  ],
  [
    'Approve',
    'design-system-patterns-campaign-dialogs--approve',
    'approved / draft / empty / busy / unsaved; паузы и длинные реплики',
    'modal / controls / badge; последовательность пузырей — композиция страницы',
  ],
  [
    'ApiKeyField',
    'design-system-patterns-apikeyfield--stored',
    'stored / unset / disabled; ввод / показ / очистка',
    'form labelGap / controls / iconButton; рисунок глаза — локальный SVG',
  ],
  [
    'SegmentedControl',
    'design-system-components-segmentedcontrol--tray',
    'tray / pill / outline / disabled; arrows / Home / End',
    'segmented wrap / option geometry',
  ],
  [
    'RuntimePipeline',
    'design-system-patterns-runtime-pipeline--running',
    'running / stopped и существующие Controls',
    'card / statTile / controls; строки журнала — собственная композиция',
  ],
  [
    'LaunchPipeline',
    'design-system-patterns-launch-readiness--ready',
    'ready / blocked / running; реальные счётчики',
    'card / statTile / controls',
  ],
  [
    'LogTerminal',
    'design-system-patterns-activity-log--populated',
    'populated / empty; события и подтверждение очистки',
    'card / controls; строки журнала — собственная композиция',
  ],
  [
    'SelectableCard',
    'design-system-patterns-campaign-selection--default',
    'selected / actions open; focus',
    'selectableCard settings',
  ],
  [
    'TabList',
    'design-system-components-tablist--profile-sections',
    'выбранная вкладка / keyboard arrows / Home / End',
    'tabList settings',
  ],
  [
    'Повторные потребители',
    'design-system-components-managed-geometry--propagation',
    'два Card / FormField / Button; измерение геометрии и HMR',
    'card.padding.default / controls.height.md / form.labelGap / layout.sectionGap.default',
  ],
  [
    'Контраст действий',
    'design-system-components-button--contrast-canvas',
    'ghostAction / refreshSuccess / refreshDanger, normal / hover',
    'семантические пары цвета текста и фона',
  ],
] as const;

function CoverageMatrix() {
  return (
    <main className="mx-auto max-w-shell p-lg">
      <h1 className="mb-lg type-page-title">Interface coverage audit</h1>
      <SectionStack>
        <Card>
          <p className="type-prose">
            Открой Foundations для значений из кода и места их редактирования. Controls меняют
            только пример. Сохранение файла tokens/components.ts или tokens/layout.ts обновляет
            приложение и холст через HMR; общий размер меняется сразу у повторных потребителей.
          </p>
        </Card>
        {cases.map(([name, id, states, settings]) => (
          <Card key={name} title={name}>
            <p className="type-prose">{states}</p>
            <p className="mt-sm type-caption">Общие настройки: {settings}</p>
            <a
              className="mt-md inline-block text-info-strong underline"
              href={`?id=${id}&viewMode=story`}
            >
              Открыть реальный холст
            </a>
          </Card>
        ))}
        <Card title="Исключения и проверка">
          <p className="type-prose">
            Уникальные рисунки и композиции остаются у владельца. Точные исключения для motion
            offsets, drawer width и tooltip anchors объяснены рядом с рецептами; они не добавляют
            одиночные ступени в базовую шкалу. Browser smoke проверяет desktop/mobile, реальные
            диалоги, клавиатуру, ошибки, пустые состояния и отсутствие сетевых запросов API.
            Скриншоты — артефакты ревью; эталоны Vite catalog остаются отдельным визуальным гейтом.
          </p>
        </Card>
      </SectionStack>
    </main>
  );
}
const meta = {
  title: 'Design System/Coverage',
  component: CoverageMatrix,
  parameters: { layout: 'fullscreen' },
} satisfies Meta<typeof CoverageMatrix>;
export default meta;
type Story = StoryObj<typeof meta>;
export const Matrix: Story = {};
