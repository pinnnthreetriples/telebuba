import type { Meta, StoryObj } from '@storybook/react-vite';
import { useState } from 'react';

import {
  Button,
  Card,
  CollapsibleCard,
  FormField,
  Input,
  ModalBody,
  ModalFooter,
  ModalHeader,
  SearchInput,
  SectionStack,
  SettingRow,
  StatTile,
} from '@/shared/ui';
import type { CardPadding, ControlSize } from '@/shared/design-system';

function Geometry({ padding, size }: { padding: CardPadding; size: Exclude<ControlSize, 'lg'> }) {
  const [search, setSearch] = useState('');
  return (
    <main className="mx-auto max-w-page px-lg py-2xl">
      <h1 className="mb-lg type-page-title">Управляемые компоненты</h1>
      <p className="mb-xl type-prose">
        Настройки: tokens/components.ts. Расстояния между секциями: tokens/layout.ts. Controls
        меняют параметры примера; общие настройки редактируются в коде.
      </p>
      <SectionStack>
        <Card padding={padding} title="Карточка" subtitle="card.padding → выбранный вариант">
          <SettingRow
            first
            htmlFor="sample-name"
            label="Название"
            hint="form.labelGap и типографическая роль label"
          >
            <Input
              id="sample-name"
              size={size}
              placeholder="Длинное название для проверки переноса"
            />
          </SettingRow>
          <SettingRow label="Действия">
            <Button size={size}>Сохранить</Button>
            <Button size={size} loading>
              Сохраняю
            </Button>
            <Button size={size} disabled>
              Недоступно
            </Button>
          </SettingRow>
        </Card>
        <CollapsibleCard
          header="Раскрывающаяся карточка"
          label="Развернуть карточку"
          defaultOpen
          headerDivider
        >
          <p className="type-prose">
            collapsible.headerPadding и collapsible.bodyPadding управляются отдельно.
          </p>
        </CollapsibleCard>
        <Card title="Поиск и статистика">
          <SearchInput
            aria-label="Поиск"
            placeholder="Поиск"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            onClear={() => setSearch('')}
            clearLabel="Очистить поиск"
          />
          <div className="mt-lg grid grid-cols-1 gap-md sm:grid-cols-3">
            <StatTile label="Готовы" value={24} tone="success" />
            <StatTile label="В работе" value={12} tone="action" />
            <StatTile label="Ошибки" value={2} tone="danger" />
          </div>
        </Card>
        <Card padding="none" className="overflow-hidden">
          <ModalHeader>
            <h2 className="type-dialog-title">Части диалога</h2>
          </ModalHeader>
          <ModalBody gap="default">
            <p className="type-dialog-body">
              modal.headerPadding, bodyPadding и footerPadding определяют геометрию всех диалогов с
              соответствующим вариантом.
            </p>
            <Input invalid aria-label="Поле с ошибкой" placeholder="Ошибка" />
          </ModalBody>
          <ModalFooter>
            <Button>Отмена</Button>
            <Button variant="primary">Сохранить</Button>
          </ModalFooter>
        </Card>
      </SectionStack>
    </main>
  );
}
const meta = {
  title: 'Design System/Components/Managed geometry',
  component: Geometry,
  tags: ['autodocs'],
  parameters: { layout: 'fullscreen' },
  args: { padding: 'default', size: 'md' },
  argTypes: {
    padding: {
      control: 'select',
      options: ['default', 'compact', 'none', 'empty', 'panel', 'vertical'],
    },
    size: { control: 'select', options: ['xs', 'sm', 'md'] },
  },
} satisfies Meta<typeof Geometry>;
export default meta;
type Story = StoryObj<typeof meta>;
export const All: Story = {};
export const Compact: Story = { args: { padding: 'compact', size: 'sm' } };

function PropagationExample() {
  const [value, setValue] = useState('');
  return (
    <main className="p-lg">
      <h1 className="mb-lg type-page-title">Propagation canvas</h1>
      <SectionStack data-testid="propagation-stack">
        {[1, 2].map((index) => (
          <Card key={index} data-testid={`propagation-card-${index}`}>
            <FormField
              label={`Длинная подпись поля ${index} для проверки переноса на мобильном экране`}
              field={{
                name: `propagation-input-${index}`,
                state: { value, meta: { isTouched: false, errors: [] } },
                handleChange: setValue,
                handleBlur: () => {},
              }}
              data-testid={`propagation-input-${index}`}
            />
            <div className="mt-lg">
              <Button data-testid={`propagation-button-${index}`}>Сохранить</Button>
            </div>
          </Card>
        ))}
      </SectionStack>
    </main>
  );
}
export const Propagation: Story = { render: () => <PropagationExample /> };
