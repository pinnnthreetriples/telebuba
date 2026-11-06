import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { createDefaultDesign } from '../model/design';
import type { StoryDesign } from '../model/types';
import { PremiumHint } from './PremiumHint';
import { StickerPanel } from './StickerPanel';
import { StoryComposer } from './StoryComposer';
import { StoryOptions } from './StoryOptions';

function Composer({
  premium,
  initial,
  onDesign,
  nonPremiumCount,
}: {
  premium: boolean | null;
  initial?: Partial<StoryDesign>;
  onDesign?: (design: StoryDesign) => void;
  nonPremiumCount?: number;
}) {
  const [design, setDesign] = useState<StoryDesign>({ ...createDefaultDesign(), ...initial });
  return (
    <StoryComposer
      media={{ kind: 'image', url: 'a.jpg' }}
      design={design}
      premium={premium}
      nonPremiumCount={nonPremiumCount}
      onChange={(next) => {
        setDesign(next);
        onDesign?.(next);
      }}
    />
  );
}

function lastDesign(spy: ReturnType<typeof vi.fn>): StoryDesign {
  return spy.mock.calls.at(-1)?.[0] as StoryDesign;
}

// The period and audience are dropdowns: open the trigger, then pick a row.
async function pickOption(trigger: string, option: string) {
  await userEvent.click(screen.getByRole('combobox', { name: trigger }));
  await userEvent.click(screen.getByRole('option', { name: option }));
}

test('options change the design: period, pin, forwarding and audience', async () => {
  const spy = vi.fn();
  render(<Composer premium onDesign={spy} />);

  await pickOption('Срок показа', '48 ч');
  expect(lastDesign(spy).period_hours).toBe(48);
  await userEvent.click(screen.getByRole('switch', { name: 'Закрепить в профиле' }));
  expect(lastDesign(spy).pinned).toBe(true);
  await userEvent.click(screen.getByRole('switch', { name: 'Запретить пересылку' }));
  expect(lastDesign(spy).protect_content).toBe(true);
  await pickOption('Кто увидит', 'Близкие друзья');
  expect(lastDesign(spy).privacy_preset).toBe('close_friends');
  expect(lastDesign(spy).period_hours).toBe(48);
});

test('only the non-default periods carry a Premium mark, and not for a Premium account', () => {
  const props = {
    design: createDefaultDesign(),
    onChange: () => undefined,
  };
  const { rerender } = render(<StoryOptions {...props} premium={false} />);
  expect(screen.getByRole('option', { name: '48 ч · Premium' })).toBeInTheDocument();
  expect(screen.getByRole('option', { name: '6 ч · Premium' })).toBeInTheDocument();
  expect(screen.getByRole('option', { name: '24 ч' })).toBeInTheDocument();

  rerender(<StoryOptions {...props} premium />);
  expect(screen.queryByText(/Premium/)).toBeNull();
});

test('stickers: add up to three, then the button stops', async () => {
  const spy = vi.fn();
  render(<Composer premium onDesign={spy} />);

  const add = screen.getByRole('button', { name: 'Добавить ссылку' });
  await userEvent.click(add);
  await userEvent.click(add);
  await userEvent.click(add);

  expect(lastDesign(spy).link_stickers).toHaveLength(3);
  expect(add).toBeDisabled();
  expect(add).toHaveAttribute('title', 'Не больше 3 ссылок');
  expect(screen.getAllByRole('button', { name: /Ссылка:/ })).toHaveLength(3);
});

test('a sticker takes a url, a label and a style, and can be deleted', async () => {
  const spy = vi.fn();
  render(<Composer premium onDesign={spy} />);
  await userEvent.click(screen.getByRole('button', { name: 'Добавить ссылку' }));

  const url = screen.getByLabelText('Ссылка 1: Адрес');
  await userEvent.type(url, 'example.com');
  await userEvent.tab();
  expect(lastDesign(spy).link_stickers[0]?.url).toBe('https://example.com');

  await userEvent.type(screen.getByLabelText('Ссылка 1: Название'), 'Читать');
  expect(lastDesign(spy).link_stickers[0]?.label).toBe('Читать');
  expect(screen.getByText('6 / 40')).toBeInTheDocument();

  await userEvent.click(screen.getByRole('radio', { name: 'Тёмный' }));
  expect(lastDesign(spy).link_stickers[0]?.style).toBe('dark');

  await userEvent.click(screen.getByRole('button', { name: 'Удалить ссылку: Ссылка 1' }));
  expect(lastDesign(spy).link_stickers).toEqual([]);
});

test('a bad url and an emoji label are explained under their fields', async () => {
  render(<Composer premium />);
  await userEvent.click(screen.getByRole('button', { name: 'Добавить ссылку' }));

  await userEvent.type(screen.getByLabelText('Ссылка 1: Адрес'), 'javascript:x');
  expect(screen.getByText('Нужен адрес http, https или tg')).toBeInTheDocument();
  expect(screen.getByLabelText('Ссылка 1: Адрес')).toHaveAttribute('aria-invalid', 'true');

  await userEvent.type(screen.getByLabelText('Ссылка 1: Название'), 'Ура 🎉');
  expect(screen.getByText('Эмодзи в названии не поддерживаются')).toBeInTheDocument();
});

test('a label over the limit is flagged', async () => {
  render(<Composer premium initial={{ link_stickers: [] }} />);
  await userEvent.click(screen.getByRole('button', { name: 'Добавить ссылку' }));

  await userEvent.click(screen.getByLabelText('Ссылка 1: Название'));
  await userEvent.paste('я'.repeat(41));
  expect(screen.getByText('Слишком длинное название')).toBeInTheDocument();
  expect(screen.getByText('41 / 40')).toBeInTheDocument();
});

test('focusing a sticker card selects it and the badge follows Premium', async () => {
  const select = vi.fn();
  const sticker = {
    id: 'a',
    url: '',
    label: '',
    style: 'white' as const,
    x: 50,
    y: 50,
    scale: 1,
    rotation: 0,
  };
  const { rerender } = render(
    <StickerPanel
      stickers={[sticker]}
      onChange={() => undefined}
      selectedId={null}
      onSelect={select}
      premium={false}
    />,
  );
  expect(screen.getByText('Premium')).toBeInTheDocument();

  await userEvent.click(screen.getByLabelText('Ссылка 1: Адрес'));
  expect(select).toHaveBeenCalledWith('a');

  rerender(
    <StickerPanel
      stickers={[sticker]}
      onChange={() => undefined}
      selectedId="a"
      onSelect={select}
      premium
      disabled
    />,
  );
  expect(screen.queryByText('Premium')).toBeNull();
  expect(screen.getByLabelText('Ссылка 1: Адрес')).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Добавить ссылку' })).toBeDisabled();
});

test('no hint for a plain story, whatever the account', () => {
  render(<Composer premium={false} />);
  expect(screen.queryByRole('status')).toBeNull();
});

test('a free account is told exactly what will be stripped', async () => {
  render(<Composer premium={false} />);

  await pickOption('Срок показа', '48 ч · Premium');
  await userEvent.click(screen.getByRole('button', { name: 'Добавить ссылку' }));

  expect(screen.getByRole('status')).toHaveTextContent(
    'Без Premium будет опубликовано без: выбранного срока, ссылок',
  );
});

test('an unknown account gets the softer hint, a Premium one none', () => {
  const { rerender } = render(
    <PremiumHint premium={null} loss={['link_stickers', 'formatting']} />,
  );
  expect(screen.getByRole('status')).toHaveTextContent(
    'Если у аккаунта нет Premium, будет опубликовано без: ссылок, форматирования',
  );

  rerender(<PremiumHint premium loss={['link_stickers']} />);
  expect(screen.queryByRole('status')).toBeNull();
});

test('the long caption is named with the free limit', () => {
  render(<PremiumHint premium={false} loss={['long_caption']} freeCaptionMax={200} />);
  expect(screen.getByRole('status')).toHaveTextContent('текста длиннее 200 символов');
});

test('bulk: how many accounts lack Premium, nothing when none do', () => {
  const { rerender } = render(
    <PremiumHint premium={null} loss={['link_stickers']} nonPremiumCount={3} />,
  );
  expect(screen.getByRole('status')).toHaveTextContent(
    '3 аккаунта без Premium опубликуют без: ссылок',
  );

  rerender(<PremiumHint premium={null} loss={['link_stickers']} nonPremiumCount={1} />);
  expect(screen.getByRole('status')).toHaveTextContent('1 аккаунт без Premium опубликует без');

  rerender(<PremiumHint premium={null} loss={['link_stickers']} nonPremiumCount={0} />);
  expect(screen.queryByRole('status')).toBeNull();

  rerender(<PremiumHint premium={false} loss={[]} nonPremiumCount={4} />);
  expect(screen.queryByRole('status')).toBeNull();
});

test('the composer shows the bulk counter through its own prop', async () => {
  render(<Composer premium={null} nonPremiumCount={2} initial={{ pinned: false }} />);
  await userEvent.click(screen.getByRole('button', { name: 'Добавить ссылку' }));
  expect(screen.getByRole('status')).toHaveTextContent('2 аккаунта без Premium');
});

test('a disabled composer locks every control', () => {
  render(
    <StoryComposer
      media={null}
      design={createDefaultDesign()}
      premium
      disabled
      onChange={() => undefined}
    />,
  );
  expect(screen.getByRole('textbox')).toHaveAttribute('contenteditable', 'false');
  expect(screen.getByRole('switch', { name: 'Закрепить в профиле' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Добавить ссылку' })).toBeDisabled();
});

test('typing in the caption updates the design caption', () => {
  const spy = vi.fn();
  render(<Composer premium onDesign={spy} />);

  const editor = screen.getByRole('textbox', { name: 'Подпись' });
  editor.appendChild(document.createTextNode('Привет'));
  editor.dispatchEvent(new Event('input', { bubbles: true }));

  expect(lastDesign(spy).caption).toBe('Привет');
  expect(lastDesign(spy).entities).toEqual([]);
});
