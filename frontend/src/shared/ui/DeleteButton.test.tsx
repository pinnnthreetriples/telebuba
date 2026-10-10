import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MotionGlobalConfig } from 'motion/react';
import { useState } from 'react';
import { afterAll, beforeAll, expect, test, vi } from 'vitest';

import { expectNoAxeViolations } from './axe.test-helpers';
import { DeleteButton, type DeleteButtonStatus } from './DeleteButton';

// Exits finish at once, so a closed pill is gone from the tree when the test looks.
beforeAll(() => {
  MotionGlobalConfig.skipAnimations = true;
});
afterAll(() => {
  MotionGlobalConfig.skipAnimations = false;
});

const tile = (name: string | RegExp = 'Удалить') => screen.getByRole('button', { name });

test('pressing the tile opens the confirm pill on the safe choice, and axe is clean', async () => {
  const user = userEvent.setup();
  const { container } = render(<DeleteButton />);

  expect(tile()).toHaveAttribute('aria-expanded', 'false');
  expect(screen.queryByRole('button', { name: 'Подтвердить удаление' })).toBeNull();

  await user.click(tile());

  expect(tile()).toHaveAttribute('aria-expanded', 'true');
  const pill = document.getElementById(tile().getAttribute('aria-controls') ?? '');
  expect(pill).not.toBeNull();
  expect(screen.getByRole('button', { name: 'Подтвердить удаление' })).toBeInTheDocument();
  // Enter right after arming must keep, not delete.
  expect(screen.getByRole('button', { name: 'Оставить' })).toHaveFocus();
  await expectNoAxeViolations(container);
});

test('Escape, keep and a second press each close the pill and return focus to the tile', async () => {
  const user = userEvent.setup();
  const onCancel = vi.fn();
  render(<DeleteButton onCancel={onCancel} />);

  await user.click(tile());
  await user.keyboard('{Escape}');
  await waitFor(() => expect(screen.queryByRole('button', { name: 'Оставить' })).toBeNull());
  expect(tile()).toHaveFocus();

  await user.click(tile());
  await user.click(screen.getByRole('button', { name: 'Оставить' }));
  expect(tile()).toHaveFocus();

  await user.click(tile());
  await user.click(tile());
  expect(tile()).toHaveAttribute('aria-expanded', 'false');

  expect(onCancel).toHaveBeenCalledTimes(3);
});

test('a synchronous confirm lands at once: tick, announcement, then back to the trash', async () => {
  const user = userEvent.setup();
  const onConfirm = vi.fn();
  render(<DeleteButton onConfirm={onConfirm} resetAfter={400} />);

  await user.click(tile());
  await user.click(screen.getByRole('button', { name: 'Подтвердить удаление' }));

  expect(onConfirm).toHaveBeenCalledTimes(1);
  expect(tile('Удалено')).toHaveFocus();
  expect(screen.getByText('Удалено', { selector: '[aria-live]' })).toBeInTheDocument();
  // The tick holds, so a second press does not re-arm a delete that already happened.
  await user.click(tile('Удалено'));
  expect(tile('Удалено')).toHaveAttribute('aria-expanded', 'false');

  await waitFor(() => expect(tile()).toBeInTheDocument());
});

test('a returned promise is awaited: busy while it runs, done when it resolves', async () => {
  const user = userEvent.setup();
  let resolve: () => void = () => undefined;
  const onConfirm = () =>
    new Promise<void>((r) => {
      resolve = r;
    });
  render(<DeleteButton onConfirm={onConfirm} resetAfter={0} />);

  await user.click(tile());
  await user.click(screen.getByRole('button', { name: 'Подтвердить удаление' }));

  expect(tile('Удаляю…')).toHaveAttribute('aria-busy', 'true');
  // Pressing a busy tile does nothing.
  await user.click(tile('Удаляю…'));
  expect(tile('Удаляю…')).toHaveAttribute('aria-expanded', 'false');

  await act(async () => {
    resolve();
    await Promise.resolve();
  });
  // `resetAfter={0}` keeps the tick.
  expect(tile('Удалено')).toHaveAttribute('aria-busy', 'false');
});

test('a rejected promise goes back to the trash and hands the error to onError', async () => {
  const user = userEvent.setup();
  const failure = new Error('нет связи');
  const onError = vi.fn();
  render(<DeleteButton onConfirm={() => Promise.reject(failure)} onError={onError} />);

  await user.click(tile());
  await user.click(screen.getByRole('button', { name: 'Подтвердить удаление' }));

  await waitFor(() => expect(onError).toHaveBeenCalledWith(failure));
  expect(tile()).toHaveAttribute('aria-busy', 'false');
});

test('a throwing confirm resets too, through onError', async () => {
  const user = userEvent.setup();
  const onError = vi.fn();
  render(
    <DeleteButton
      onConfirm={() => {
        throw new Error('сломалось');
      }}
      onError={onError}
    />,
  );

  await user.click(tile());
  await user.click(screen.getByRole('button', { name: 'Подтвердить удаление' }));
  expect(onError).toHaveBeenCalledTimes(1);
  expect(tile()).toHaveAttribute('aria-expanded', 'false');
});

test('disabling closes an open prompt and the tile cannot be pressed', async () => {
  const user = userEvent.setup();
  function Harness() {
    const [disabled, setDisabled] = useState(false);
    return (
      <>
        <DeleteButton disabled={disabled} />
        <button type="button" onClick={() => setDisabled(true)}>
          выключить
        </button>
      </>
    );
  }
  render(<Harness />);

  await user.click(tile());
  expect(tile()).toHaveAttribute('aria-expanded', 'true');
  await user.click(screen.getByRole('button', { name: 'выключить' }));

  expect(tile()).toBeDisabled();
  expect(tile()).toHaveAttribute('aria-expanded', 'false');
  expect(screen.queryByRole('button', { name: 'Подтвердить удаление' })).toBeNull();
});

test('controlled: the owner decides the status and hears every request', async () => {
  const user = userEvent.setup();
  const requests: DeleteButtonStatus[] = [];
  function Harness() {
    const [status, setStatus] = useState<DeleteButtonStatus>('idle');
    return (
      <DeleteButton
        status={status}
        onStatusChange={(next) => {
          requests.push(next);
          setStatus(next);
        }}
      />
    );
  }
  render(<Harness />);

  await user.click(tile());
  await user.click(screen.getByRole('button', { name: 'Подтвердить удаление' }));
  expect(requests).toEqual(['armed', 'done']);
});

test('the pill can open on the left', async () => {
  const user = userEvent.setup();
  render(<DeleteButton side="left" />);
  await user.click(tile());
  const pill = document.getElementById(tile().getAttribute('aria-controls') ?? '');
  expect(pill?.className).toContain('flex-row-reverse');
});
