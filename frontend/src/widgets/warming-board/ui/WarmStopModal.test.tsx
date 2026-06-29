import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { WarmStopModal } from './WarmStopModal';

test('finish, keep and stop each fire the right callbacks', async () => {
  const onClose = vi.fn();
  const onStop = vi.fn();
  const onFinish = vi.fn();
  const { rerender } = render(
    <WarmStopModal phone="+79991234567" onClose={onClose} onStop={onStop} onFinish={onFinish} />,
  );
  expect(screen.getByText('Остановить прогрев?')).toBeInTheDocument();

  await userEvent.click(screen.getByText('В прогретые'));
  expect(onFinish).toHaveBeenCalledTimes(1);
  expect(onClose).toHaveBeenCalledTimes(1);

  rerender(
    <WarmStopModal phone="+79991234567" onClose={onClose} onStop={onStop} onFinish={onFinish} />,
  );
  await userEvent.click(screen.getByText('Продолжить'));
  expect(onClose).toHaveBeenCalledTimes(2);

  await userEvent.click(screen.getByText('Остановить'));
  expect(onStop).toHaveBeenCalledTimes(1);
  expect(onClose).toHaveBeenCalledTimes(3);
});
