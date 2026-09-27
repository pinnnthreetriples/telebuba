import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { expect, test } from 'vitest';

import { TabList } from './TabList';

const OPTIONS = [
  { value: 'one', label: 'Первый' },
  { value: 'two', label: 'Второй' },
  { value: 'three', label: 'Третий' },
] as const;

function Example() {
  const [value, setValue] = useState<(typeof OPTIONS)[number]['value']>('one');
  return (
    <>
      <TabList
        options={OPTIONS}
        value={value}
        onChange={setValue}
        idPrefix="example-tab"
        panelId="example-panel"
        ariaLabel="Разделы"
      />
      <div role="tabpanel" id="example-panel" aria-labelledby={`example-tab-${value}`}>
        Содержимое
      </div>
      <button type="button">После вкладок</button>
    </>
  );
}

test('keeps one tab stop and links the active tab to its panel', async () => {
  render(<Example />);
  const tabs = screen.getAllByRole('tab');
  expect(tabs.map((tab) => tab.tabIndex)).toEqual([0, -1, -1]);
  expect(screen.getByRole('tabpanel')).toHaveAttribute('aria-labelledby', 'example-tab-one');

  await userEvent.tab();
  expect(tabs[0]).toHaveFocus();
  await userEvent.tab();
  expect(screen.getByRole('button', { name: 'После вкладок' })).toHaveFocus();
});

test('arrows, Home and End move focus and select a tab', async () => {
  render(<Example />);
  await userEvent.tab();
  await userEvent.keyboard('{ArrowLeft}');
  expect(screen.getByRole('tab', { name: 'Третий' })).toHaveFocus();
  expect(screen.getByRole('tabpanel')).toHaveAttribute('aria-labelledby', 'example-tab-three');

  await userEvent.keyboard('{Home}');
  expect(screen.getByRole('tab', { name: 'Первый' })).toHaveFocus();
  await userEvent.keyboard('{ArrowRight}');
  expect(screen.getByRole('tab', { name: 'Второй' })).toHaveFocus();
  await userEvent.keyboard('{End}');
  expect(screen.getByRole('tab', { name: 'Третий' })).toHaveFocus();
});
