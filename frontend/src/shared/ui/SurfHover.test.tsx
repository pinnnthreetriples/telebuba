import { render, screen } from '@testing-library/react';
import { afterAll, expect, test } from 'vitest';

import { expectNoAxeViolations } from './axe.test-helpers';
import { SurfHover } from './SurfHover';

// The unconditional class is `group-hover:-translate-x-[…]`, so a plain substring
// check would match it too; only the un-prefixed token means "pinned open".
const PINNED = /(^|\s)-translate-x-\[var\(--shift\)\]/;

// Ширину теперь МЕРЯЕТ компонент, и это единственное, что тут стоит проверять: прежний
// тест утверждал число, которое передал вызывающий (`144px`), то есть проверял константу
// фикстуры, а не расстояние, на которое едет поверхность. Оба вызывающих передавали при
// этом неверное число — 144 против 138 у трёх кнопок `w-action`.
//
// В тестовой DOM каждый бокс 0×0, поэтому `offsetWidth` подменяется: без подмены
// утверждение «сдвиг равен ширине действий» выполнялось бы как 0 === 0 при любой поломке
// измерения.
const ACTIONS_WIDTH = 138;
const own = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'offsetWidth');

Object.defineProperty(HTMLElement.prototype, 'offsetWidth', {
  configurable: true,
  get(this: HTMLElement) {
    return this.dataset.measured === 'actions' ? ACTIONS_WIDTH : 0;
  },
});

afterAll(() => {
  if (own) Object.defineProperty(HTMLElement.prototype, 'offsetWidth', own);
});

test('the surface shifts by the measured width of the actions, not by a number it was told', async () => {
  const { container } = render(
    <SurfHover
      surfaceId="surf"
      surface={<div>row</div>}
      actions={<button type="button">pause</button>}
    />,
  );

  // Разметку измеряемой обёртки помечает сам компонент — ищем её через DOM, чтобы тест
  // не знал внутренней вложенности больше, чем нужно.
  const measured = container.querySelector('[data-measured="actions"]');
  expect(measured).not.toBeNull();

  const surface = document.getElementById('surf');
  expect(surface?.style.getPropertyValue('--shift')).toBe(`${String(ACTIONS_WIDTH)}px`);
  // Unrevealed, the actions are still rendered (they are only ever covered), so
  // they stay reachable by name for the hover/open state the caller drives.
  expect(screen.getByRole('button', { name: 'pause' })).toBeInTheDocument();
  expect(surface?.className).not.toMatch(PINNED);
  await expectNoAxeViolations(container);
});

test('open pins the reveal, so the actions are reachable without a hover', () => {
  render(<SurfHover open surfaceId="surf" surface={<div>row</div>} actions={null} />);

  expect(document.getElementById('surf')?.className).toMatch(PINNED);
});
