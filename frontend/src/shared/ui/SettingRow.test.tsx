import { render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';

import { expectNoAxeViolations } from './axe.test-helpers';
import { SectionLabel, SettingRow } from './SettingRow';

test('подпись и подсказка слева, контрол справа, разделитель сверху', () => {
  const { container } = render(
    <SettingRow label="Пауза" hint="Между сообщениями">
      <button type="button">30 сек</button>
    </SettingRow>,
  );
  const row = container.firstElementChild as HTMLElement;
  expect(row.className).toContain('border-t');
  expect(row.className).toContain('min-h-touch');
  expect(screen.getByText('Пауза').className).toContain('text-body');
  expect(screen.getByText('Между сообщениями').className).toContain('type-small');
  expect(row.lastElementChild?.textContent).toBe('30 сек');
});

test('первая строка без разделителя', () => {
  const { container } = render(
    <SettingRow first label="Тип">
      <span />
    </SettingRow>,
  );
  expect((container.firstElementChild as HTMLElement).className).not.toContain('border-t');
});

test('с htmlFor подпись — настоящий label поля', async () => {
  const { container } = render(
    <SettingRow label="Канал-образец" htmlFor="seed">
      <input id="seed" />
    </SettingRow>,
  );
  expect(screen.getByLabelText('Канал-образец').id).toBe('seed');
  await expectNoAxeViolations(container);
});

test('подпись группы: название и необязательное уточнение', () => {
  const { rerender } = render(<SectionLabel title="Цели" />);
  expect(screen.getByText('Цели').className).toContain('type-small-medium');
  rerender(<SectionLabel title="Цели" caption="3 канала" />);
  expect(screen.getByText('3 канала').className).toContain('type-small');
});
