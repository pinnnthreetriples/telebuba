import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { AccountFilterOptions } from '@/shared/api';

import { type AccountFilters, NO_FILTERS } from '../model/filters';
import { FilterChips } from './FilterChips';
import { FilterMenu } from './FilterMenu';

const OPTIONS: AccountFilterOptions = {
  phone_codes: [
    { calling_code: 7, country_code: 'RU', count: 4 },
    { calling_code: 49, country_code: 'DE', count: 2 },
  ],
  proxy_countries: [{ country_code: 'NL', country_name: 'Netherlands', count: 3 }],
  no_proxy_count: 1,
};

// The page's wiring: the menu and the chips share one filter state.
function Harness({ onChange }: { onChange: (filters: AccountFilters) => void }) {
  const [filters, setFilters] = useState<AccountFilters>(NO_FILTERS);
  const change = (next: AccountFilters) => {
    setFilters(next);
    onChange(next);
  };
  return (
    <>
      <FilterMenu filters={filters} onChange={change} options={OPTIONS} found={3} />
      <FilterChips filters={filters} onChange={change} options={OPTIONS} />
    </>
  );
}

async function openMenu() {
  await userEvent.click(screen.getByRole('button', { name: 'Расширенные фильтры' }));
  return screen.getByRole('region', { name: 'Фильтры' });
}

test('the groups are built from the filter options in data', async () => {
  render(<Harness onChange={vi.fn()} />);
  const card = await openMenu();

  const phone = within(card).getByRole('radiogroup', { name: 'Номер' });
  expect(
    within(phone)
      .getAllByRole('radio')
      .map((pill) => pill.textContent),
  ).toEqual(['Все', '+7', '+49']);
  expect(within(phone).getByRole('radio', { name: '+49 · Германия' })).toBeInTheDocument();
  const proxy = within(card).getByRole('radiogroup', { name: 'Прокси' });
  expect(within(proxy).getByRole('radio', { name: 'Нидерланды' })).toBeInTheDocument();
  expect(within(proxy).getByRole('radio', { name: 'Без прокси' })).toBeInTheDocument();
  const status = within(card).getByRole('radiogroup', { name: 'Статус' });
  expect(within(status).getAllByRole('radio')).toHaveLength(5);
  expect(within(card).getByText('Найдено: 3 аккаунта')).toBeInTheDocument();
});

test('a pill applies at once, shows as a chip, and the chip removes it', async () => {
  const onChange = vi.fn();
  render(<Harness onChange={onChange} />);
  const card = await openMenu();

  await userEvent.click(within(card).getByRole('radio', { name: 'Нидерланды' }));
  expect(onChange).toHaveBeenLastCalledWith({ ...NO_FILTERS, proxy: 'NL' });
  expect(within(card).getByRole('radio', { name: 'Нидерланды' })).toHaveAttribute(
    'aria-checked',
    'true',
  );
  await userEvent.click(within(card).getByRole('radio', { name: '70+' }));
  expect(onChange).toHaveBeenLastCalledWith({ ...NO_FILTERS, proxy: 'NL', minTrust: 70 });

  expect(screen.getByRole('button', { name: 'Убрать фильтр «Прокси: Нидерланды»' })).toBeVisible();
  await userEvent.click(screen.getByRole('button', { name: 'Убрать фильтр «Trust ≥ 70»' }));
  expect(onChange).toHaveBeenLastCalledWith({ ...NO_FILTERS, proxy: 'NL' });
});

test('chips name the phone code, the status and «no proxy»; «Сбросить всё» clears them', async () => {
  const onChange = vi.fn();
  render(<Harness onChange={onChange} />);
  const card = await openMenu();
  await userEvent.click(within(card).getByRole('radio', { name: '+7 · Россия' }));
  await userEvent.click(within(card).getByRole('radio', { name: 'Без прокси' }));
  await userEvent.click(within(card).getByRole('radio', { name: 'Нужен код' }));

  expect(screen.getByText('Номер: +7')).toBeInTheDocument();
  expect(screen.getByText('Прокси: Без прокси')).toBeInTheDocument();
  expect(screen.getByText('Статус: Нужен код')).toBeInTheDocument();

  await userEvent.click(screen.getByRole('button', { name: 'Сбросить всё' }));
  expect(onChange).toHaveBeenLastCalledWith(NO_FILTERS);
  expect(screen.queryByText('Номер: +7')).not.toBeInTheDocument();
});

test('the card header resets everything, and only shows when something is set', async () => {
  const onChange = vi.fn();
  render(<Harness onChange={onChange} />);
  const card = await openMenu();
  expect(within(card).queryByRole('button', { name: 'Сбросить' })).not.toBeInTheDocument();
  await userEvent.click(within(card).getByRole('radio', { name: '50+' }));
  await userEvent.click(within(card).getByRole('button', { name: 'Сбросить' }));
  expect(onChange).toHaveBeenLastCalledWith(NO_FILTERS);
});

test('«Показать», Escape and a click outside close the card', async () => {
  render(<Harness onChange={vi.fn()} />);
  const button = screen.getByRole('button', { name: 'Расширенные фильтры' });

  let card = await openMenu();
  await userEvent.click(within(card).getByRole('button', { name: 'Показать' }));
  expect(screen.queryByRole('region', { name: 'Фильтры' })).not.toBeInTheDocument();
  expect(button).toHaveFocus();

  await openMenu();
  fireEvent.keyDown(document, { key: 'Escape' });
  expect(screen.queryByRole('region', { name: 'Фильтры' })).not.toBeInTheDocument();

  card = await openMenu();
  fireEvent.pointerDown(card);
  expect(screen.getByRole('region', { name: 'Фильтры' })).toBeInTheDocument();
  fireEvent.pointerDown(document.body);
  expect(screen.queryByRole('region', { name: 'Фильтры' })).not.toBeInTheDocument();
});
