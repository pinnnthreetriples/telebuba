import type { Meta, StoryObj } from '@storybook/react-vite';

import {
  componentSettings,
  layoutSettings,
  motion,
  primitives,
  semantic,
  spacing,
  typography,
} from '@/shared/design-system';
import { Card, SectionStack } from '@/shared/ui';

const GROUPS = [
  ['primitives.ts', 'Цвета, радиусы, шрифты и тени', primitives],
  ['semantic.ts', 'Назначение цветов', semantic],
  ['spacing.ts', 'Отступы, размеры и адаптивность', spacing],
  ['typography.ts', 'Типографика', typography],
  ['motion.ts', 'Движение', motion],
  ['components.ts', 'Геометрия компонентов', componentSettings],
  ['layout.ts', 'Раскладка страниц', layoutSettings],
] as const;

function entries(value: unknown, prefix = ''): [string, string][] {
  if (value !== null && typeof value === 'object' && !Array.isArray(value)) {
    return Object.entries(value).flatMap(([key, nested]) =>
      entries(nested, prefix ? `${prefix}.${key}` : key),
    );
  }
  return [[prefix, Array.isArray(value) ? value.join(', ') : String(value)]];
}

function Foundations() {
  return (
    <main className="mx-auto max-w-shell px-lg py-page">
      <h1 className="mb-lg type-page-title">Настройки дизайн-системы</h1>
      <p className="mb-2xl type-prose">
        Редактируй нужный файл в src/shared/design-system/tokens. Этот холст и приложение используют
        одни настройки. Числа меняются в базовых шкалах; компоненты ссылаются на названия их
        ступеней.
      </p>
      <SectionStack>
        {GROUPS.map(([file, title, settings]) => (
          <Card key={file}>
            <h2 className="type-card-title">{title}</h2>
            <div className="mb-lg type-caption">
              Файл: <code>{file}</code>
            </div>
            <dl className="grid min-w-0 grid-cols-1 gap-sm md:grid-cols-2">
              {entries(settings).map(([name, value]) => (
                <div key={name} className="min-w-0 rounded-md bg-canvas px-md py-sm">
                  <dt className="break-all font-mono type-caption">{name}</dt>
                  <dd className="break-words type-value">{value}</dd>
                </div>
              ))}
            </dl>
          </Card>
        ))}
      </SectionStack>
    </main>
  );
}

const meta = {
  title: 'Design System/Foundations',
  component: Foundations,
  parameters: { layout: 'fullscreen' },
} satisfies Meta<typeof Foundations>;
export default meta;
type Story = StoryObj<typeof meta>;
export const All: Story = {};
