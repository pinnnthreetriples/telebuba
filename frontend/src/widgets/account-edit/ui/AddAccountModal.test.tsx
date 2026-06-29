import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { AddAccountModal } from './AddAccountModal';

test('stepper: method → next → proxy choice → manual form → back', async () => {
  const onClose = vi.fn();
  const onImport = vi.fn();
  render(<AddAccountModal onClose={onClose} onImport={onImport} />);
  expect(screen.getByText('Добавить аккаунт')).toBeInTheDocument();

  // Next is disabled until a method is picked
  const next = screen.getByText('Далее');
  expect(next).toBeDisabled();

  // pick the .session method → dropzone appears
  await userEvent.click(screen.getByText('Файл .session'));
  expect(screen.getByText('Обзор')).toBeInTheDocument();
  expect(next).toBeEnabled();

  // advance to step 2 (proxy choice)
  await userEvent.click(next);
  expect(screen.getByText('Аккаунт добавлен. Назначьте прокси для работы.')).toBeInTheDocument();

  // open manual proxy form, then back to choice
  await userEvent.click(screen.getByText('Добавить прокси'));
  expect(screen.getByText('Хост')).toBeInTheDocument();
  await userEvent.click(screen.getByText('Назад'));

  // open the pool list, then back
  await userEvent.click(screen.getByText('Выбрать из пула'));
  expect(screen.getByText('nl-1.proxyhub.net:1080')).toBeInTheDocument();
  await userEvent.click(screen.getByText('Назад'));

  // back to step 1 from the proxy choice
  await userEvent.click(screen.getByText('Назад'));
  expect(screen.getByText('Шаг 1 · способ добавления')).toBeInTheDocument();
});

test('tdata method uploads a file and fires onImport', async () => {
  const onClose = vi.fn();
  const onImport = vi.fn();
  render(<AddAccountModal onClose={onClose} onImport={onImport} />);

  await userEvent.click(screen.getByText('Архив tdata.zip'));
  const input = document.body.querySelector('input[type="file"]') as HTMLInputElement;
  const file = new File(['x'], 'acc.zip', { type: 'application/zip' });
  fireEvent.change(input, { target: { files: [file] } });

  expect(onImport).toHaveBeenCalledTimes(1);
  expect(screen.getByText('acc.zip')).toBeInTheDocument();
  expect(screen.getByText('Файл готов')).toBeInTheDocument();
});

test('cancel on step 1 closes', async () => {
  const onClose = vi.fn();
  render(<AddAccountModal onClose={onClose} onImport={vi.fn()} />);
  await userEvent.click(screen.getByText('Отмена'));
  expect(onClose).toHaveBeenCalledTimes(1);
});

test('pool selection and skip close the wizard', async () => {
  const onClose = vi.fn();
  render(<AddAccountModal onClose={onClose} onImport={vi.fn()} />);
  await userEvent.click(screen.getByText('Файл .session'));
  await userEvent.click(screen.getByText('Далее'));

  // skip on choice step closes
  await userEvent.click(screen.getByText('Пропустить'));
  expect(onClose).toHaveBeenCalledTimes(1);
});

test('done from the manual proxy form closes', async () => {
  const onClose = vi.fn();
  render(<AddAccountModal onClose={onClose} onImport={vi.fn()} />);
  await userEvent.click(screen.getByText('Файл .session'));
  await userEvent.click(screen.getByText('Далее'));
  await userEvent.click(screen.getByText('Добавить прокси'));
  await userEvent.click(screen.getByText('Готово'));
  expect(onClose).toHaveBeenCalledTimes(1);
});

test('picking a pool proxy closes the wizard', async () => {
  const onClose = vi.fn();
  render(<AddAccountModal onClose={onClose} onImport={vi.fn()} />);
  await userEvent.click(screen.getByText('Файл .session'));
  await userEvent.click(screen.getByText('Далее'));
  await userEvent.click(screen.getByText('Выбрать из пула'));
  await userEvent.click(screen.getByText('nl-1.proxyhub.net:1080'));
  expect(onClose).toHaveBeenCalledTimes(1);
});
