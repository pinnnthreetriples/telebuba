// Контролы: Button, IconButton, Input, Textarea, Select, Switch, SegmentedControl.
//
// Каждый показан во всех размерах и видах, что объявляет сам компонент, плюс те
// состояния, которые задаются пропом: `disabled`, `loading`, `invalid`. Состояния,
// которые задаёт браузер — `hover`, `focus`, `active` — помечены `probe` и снимаются
// тестом.
import { useState } from 'react';

import {
  Button,
  Icon,
  IconButton,
  Input,
  SegmentedControl,
  Select,
  Switch,
  Textarea,
} from '@/shared/ui';

import { Cell, Row, Section } from './Frame';

const BUTTON_VARIANTS = [
  'primary',
  'neutral',
  'secondary',
  'danger',
  'ghost',
  'dashed',
  'dashedMuted',
] as const;
const BUTTON_SIZES = ['md', 'sm', 'xs', 'block'] as const;
const ICON_SIZES = ['sm', 'md', 'lg', 'touch'] as const;
const ICON_TONES = ['neutral', 'primary', 'danger'] as const;
const FIELD_SIZES = ['md', 'sm', 'xs'] as const;
const SEG_VARIANTS = ['tray', 'pill', 'outline'] as const;

const SEG_OPTIONS = [
  { value: 'all', label: 'Все' },
  { value: 'live', label: 'В работе' },
  { value: 'off', label: 'Стоп' },
] as const;

const SELECT_OPTIONS = [
  { value: 'socks5', label: 'SOCKS5' },
  { value: 'http', label: 'HTTP' },
  { value: 'mtproto', label: 'MTProto', disabled: true },
];

export function Controls() {
  const [seg, setSeg] = useState<'all' | 'live' | 'off'>('live');
  const [proxy, setProxy] = useState('socks5');
  const [empty, setEmpty] = useState('');
  const [on, setOn] = useState(true);
  const [off, setOff] = useState(false);

  return (
    <Section
      id="controls"
      title="Контролы"
      note="Button, IconButton, Input, Select, Switch и SegmentedControl. Высота, радиус, типографика, фокус, disabled и invalid приходят из общего рецепта контрола — размеры sm/md/lg означают одно и то же у всех."
    >
      {BUTTON_VARIANTS.map((variant) => (
        <Row key={variant} label={`Button · ${variant}`}>
          {BUTTON_SIZES.map((size) => (
            <Cell key={size} caption={size}>
              <div className={size === 'block' ? 'w-menu' : undefined}>
                <Button variant={variant} size={size}>
                  Сохранить
                </Button>
              </div>
            </Cell>
          ))}
        </Row>
      ))}

      <Row label="Button · состояния" hint="hover, focus и зажатие вызывает тест">
        <Cell caption="hover" probe="hover">
          <Button variant="primary">Наведение</Button>
        </Cell>
        <Cell caption="focus-visible" probe="focus">
          <Button variant="primary">Фокус</Button>
        </Cell>
        {/* Зажатие делит заливку с наведением — у Button нет отдельного `active:`. Это
            намеренно (три синих на одну кнопку операторская панель не просит), но видно
            это стало только после того, как проба научилась ДЕРЖАТЬ кнопку нажатой:
            `click()` отпускал её до снимка, и «active» показывал покой. */}
        <Cell caption="active = hover" probe="press">
          <Button variant="primary">Нажатие</Button>
        </Cell>
        {/* Три тона кольца, а не один: тон следует за заливкой, и «синяя дуга на синем»
            — тот дефект, из-за которого тон вообще перестали передавать вручную. Видно
            это только рядом. */}
        <Cell caption="loading · primary">
          <Button variant="primary" loading>
            Сохраняю…
          </Button>
        </Cell>
        <Cell caption="loading · secondary">
          <Button loading>Проверяю…</Button>
        </Cell>
        <Cell caption="loading · danger">
          <Button variant="danger" loading>
            Удаляю…
          </Button>
        </Cell>
        <Cell caption="disabled">
          <Button variant="primary" disabled>
            Недоступно
          </Button>
        </Cell>
        <Cell caption="secondary disabled">
          <Button variant="secondary" disabled>
            Недоступно
          </Button>
        </Cell>
      </Row>

      {/* Форма — ось, а не ступень, поэтому круг показан на КАЖДОМ размере: строка
          доказывает, что он composes с любым, а не заменяет один из них. Раньше круг был
          побочным эффектом ступени `lg`, и увидеть это на снимке было нельзя. */}
      <Row label="IconButton · форма" hint="квадрат по умолчанию, круг — по запросу">
        {ICON_SIZES.map((size) => (
          <Cell key={size} caption={`${size} · circle`}>
            <IconButton size={size} shape="circle" aria-label="Изменить">
              <Icon name="pencil" size={14} />
            </IconButton>
          </Cell>
        ))}
      </Row>

      {ICON_TONES.map((tone) => (
        <Row key={tone} label={`IconButton · ${tone}`}>
          {ICON_SIZES.map((size) => (
            <Cell key={size} caption={size}>
              <IconButton size={size} tone={tone} aria-label="Изменить">
                <Icon name="pencil" size={14} />
              </IconButton>
            </Cell>
          ))}
          <Cell caption="hover" probe="hover">
            <IconButton tone={tone} aria-label="Изменить">
              <Icon name="pencil" size={14} />
            </IconButton>
          </Cell>
          <Cell caption="focus-visible" probe="focus">
            <IconButton tone={tone} aria-label="Изменить">
              <Icon name="pencil" size={14} />
            </IconButton>
          </Cell>
          <Cell caption="disabled">
            <IconButton tone={tone} aria-label="Изменить" disabled>
              <Icon name="pencil" size={14} />
            </IconButton>
          </Cell>
        </Row>
      ))}

      <Row label="Input · размеры">
        {FIELD_SIZES.map((size) => (
          <Cell key={size} caption={size}>
            <div className="w-menu">
              <Input size={size} defaultValue="ivan.petrov" />
            </div>
          </Cell>
        ))}
      </Row>

      <Row label="Input · состояния">
        <Cell caption="placeholder">
          <div className="w-menu">
            <Input placeholder="+7 900 000-00-00" />
          </div>
        </Cell>
        <Cell caption="flat">
          <div className="w-menu">
            <Input tone="flat" defaultValue="Только чтение" readOnly />
          </div>
        </Cell>
        <Cell caption="invalid">
          <div className="w-menu">
            <Input invalid defaultValue="не телефон" />
          </div>
        </Cell>
        <Cell caption="disabled">
          <div className="w-menu">
            <Input disabled defaultValue="Недоступно" />
          </div>
        </Cell>
        <Cell caption="focus-within" probe="focus">
          <div className="w-menu">
            <Input defaultValue="Фокус" />
          </div>
        </Cell>
      </Row>

      <Row label="Textarea">
        <Cell caption="md">
          <div className="w-panel">
            <Textarea rows={3} defaultValue="Промпт для генерации комментария." />
          </div>
        </Cell>
        <Cell caption="invalid">
          <div className="w-panel">
            <Textarea rows={3} invalid defaultValue="" placeholder="Обязательное поле" />
          </div>
        </Cell>
      </Row>

      <Row label="Select">
        <Cell caption="значение">
          <div className="w-menu">
            <Select
              value={proxy}
              onChange={setProxy}
              options={SELECT_OPTIONS}
              ariaLabel="Протокол"
            />
          </div>
        </Cell>
        <Cell caption="placeholder">
          <div className="w-menu">
            <Select
              value={empty}
              onChange={setEmpty}
              options={SELECT_OPTIONS}
              placeholder="Выберите протокол"
              ariaLabel="Протокол"
            />
          </div>
        </Cell>
        <Cell caption="disabled">
          <div className="w-menu">
            <Select
              value={proxy}
              onChange={setProxy}
              options={SELECT_OPTIONS}
              disabled
              ariaLabel="Протокол"
            />
          </div>
        </Cell>
        <Cell caption="открыт" probe="open">
          <div className="w-menu">
            <Select
              value={proxy}
              onChange={setProxy}
              options={SELECT_OPTIONS}
              ariaLabel="Протокол"
            />
          </div>
        </Cell>
      </Row>

      <Row label="Switch">
        <Cell caption="включён">
          <Switch checked={on} onChange={setOn} label="Автоответ" />
        </Cell>
        <Cell caption="выключен">
          <Switch checked={off} onChange={setOff} label="Автоответ" />
        </Cell>
        <Cell caption="disabled">
          <Switch checked={on} onChange={setOn} label="Автоответ" disabled />
        </Cell>
        <Cell caption="focus-visible" probe="focus">
          <Switch checked={off} onChange={setOff} label="Автоответ" />
        </Cell>
      </Row>

      {SEG_VARIANTS.map((variant) => (
        <Row key={variant} label={`SegmentedControl · ${variant}`}>
          <Cell caption="обычный">
            <div className="w-panel">
              <SegmentedControl
                variant={variant}
                value={seg}
                onChange={setSeg}
                options={SEG_OPTIONS}
                ariaLabel="Фильтр"
              />
            </div>
          </Cell>
          <Cell caption="disabled">
            <div className="w-panel">
              <SegmentedControl
                variant={variant}
                value={seg}
                onChange={setSeg}
                options={SEG_OPTIONS}
                disabled
                ariaLabel="Фильтр"
              />
            </div>
          </Cell>
        </Row>
      ))}
    </Section>
  );
}
