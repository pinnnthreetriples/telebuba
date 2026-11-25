import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { expect, test } from 'vitest';

import '@/shared/i18n';

import { HourTip, JoinTip, RestTip } from './PaceTips';

// The dialog owns the values; a tip only reads them and proposes one.
function OwnedHour() {
  const [pause, setPause] = useState({ min: 30, max: 90 });
  return <HourTip betweenChats={pause} perHour={20} onApply={setPause} />;
}

test('a burst is named, drawn, and fixed by the button', async () => {
  render(<OwnedHour />);
  expect(screen.getByRole('img')).toHaveAccessibleName(/20 писем за 20 мин, потом спит 40 мин/);
  expect(screen.getByText('спит 40 мин')).toBeInTheDocument();

  await userEvent.click(screen.getByRole('button', { name: 'Поставить' }));

  expect(screen.getByText('Отлично: письма идут ровно весь час.')).toBeInTheDocument();
  // Advice that is already followed is not offered again.
  expect(screen.queryByRole('button')).not.toBeInTheDocument();
});

test('without an account limit there is nothing to recommend against', () => {
  render(<HourTip betweenChats={{ min: 60, max: 60 }} perHour={null} onApply={() => {}} />);
  expect(screen.getByText('Лимит выключен: около 60 писем в час.')).toBeInTheDocument();
  expect(screen.queryByRole('button')).not.toBeInTheDocument();
});

test('writing right after a join is warned against and the hour is one click away', async () => {
  let applied: number | null = null;
  render(
    <JoinTip
      minutes={0}
      onApply={(minutes) => {
        applied = minutes;
      }}
    />,
  );
  expect(screen.getByText('Не ждёт')).toBeInTheDocument();

  await userEvent.click(screen.getByRole('button', { name: 'Поставить' }));

  expect(applied).toBe(60);
});

test('a rest of an hour or more needs no advice', () => {
  render(<RestTip minutes={{ min: 60, max: 120 }} onApply={() => {}} />);
  expect(screen.getByText('Отдых 60–120 мин')).toBeInTheDocument();
  expect(screen.queryByRole('button')).not.toBeInTheDocument();
});
