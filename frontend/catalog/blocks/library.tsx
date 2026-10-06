// «Библиотека» — блоки `shared/ui`, которые пришли раньше своего экрана: три компонента,
// перенесённые из Devigner UI вместе с их анимацией (DeleteButton, InlineTimeEdit,
// DateRangePicker). На странице блоков они стоят в конце и вместо «где используется»
// помечены библиотекой: выдумывать им места вызова ради строчки в документе значило бы
// врать документом. Как только блок встанет на экран, генератор потребует снять пометку.
//
// Образцы статические — `react-dom/server` не исполняет анимаций, — поэтому у каждого
// компонента показано состояние покоя и раскрытое состояние, заданное пропом
// (`defaultStatus`, `defaultOpen`, `defaultValue`). День «сегодня» прибит: страница,
// собранная завтра, не должна расходиться с собранной сегодня.
import { DateRangePicker, DeleteButton, InlineTimeEdit } from '@/shared/ui';

import type { BlockDemo } from './Blocks';
import { InCard } from './frames';

const TODAY = new Date(2026, 9, 6);

export const LIBRARY_BLOCKS: BlockDemo[] = [
  {
    id: 'delete-button',
    name: 'DeleteButton',
    library: true,
    variants: [
      { label: 'в покое', node: <DeleteButton /> },
      { label: 'подтверждение', node: <DeleteButton defaultStatus="armed" /> },
      { label: 'удалено', node: <DeleteButton defaultStatus="done" resetAfter={0} /> },
    ],
  },
  {
    id: 'inline-time-edit',
    name: 'InlineTimeEdit',
    library: true,
    variants: [
      {
        label: 'в покое',
        node: (
          <InCard>
            <InlineTimeEdit defaultValue={150} />
          </InCard>
        ),
      },
      {
        label: 'редактирование',
        node: (
          <InCard>
            <InlineTimeEdit defaultValue={150} defaultOpen />
          </InCard>
        ),
      },
    ],
  },
  {
    id: 'date-range-picker',
    name: 'DateRangePicker',
    library: true,
    variants: [
      {
        label: 'выбран диапазон',
        node: (
          <DateRangePicker
            today={TODAY}
            minDate={TODAY}
            defaultValue={{ start: new Date(2026, 9, 9), end: new Date(2026, 9, 12) }}
          />
        ),
      },
      {
        label: 'пусто',
        node: <DateRangePicker today={TODAY} minDate={TODAY} />,
      },
    ],
  },
];
