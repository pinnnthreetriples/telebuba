import { render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';

import { expectNoAxeViolations } from './axe.test-helpers';
import { Stepper, type StepperStep } from './Stepper';

const STEPS: StepperStep[] = [
  { id: 'a', label: 'Подписка', state: 'done' },
  { id: 'b', label: 'Чтение', state: 'current' },
  { id: 'c', label: 'Реакции', state: 'upcoming' },
];

function fills(container: HTMLElement): string[] {
  // Each cell holds two rail halves; the inner span of each is the fill.
  return [...container.querySelectorAll('li [aria-hidden="true"] > span > span')].map(
    (fill) => fill.className,
  );
}

test('the current step is announced as the current step, and only it', async () => {
  const { container } = render(<Stepper steps={STEPS} label="Цикл" />);
  const items = screen.getAllByRole('listitem');
  expect(items.map((item) => item.getAttribute('aria-current'))).toEqual([null, 'step', null]);
  expect(screen.getByRole('list', { name: 'Цикл' })).toBeInTheDocument();
  await expectNoAxeViolations(container);
});

test('a rail segment takes the state of the step it leads into', () => {
  const { container } = render(<Stepper steps={STEPS} />);
  // Cell a: [hidden, → b], cell b: [→ b, → c], cell c: [→ c, hidden].
  const classes = fills(container);
  expect(classes[1]).toContain('bg-action-primary');
  expect(classes[2]).toContain('bg-action-primary');
  expect(classes[3]).toContain('w-0');
  expect(classes[4]).toContain('w-0');

  const allDone = STEPS.map((step) => ({ ...step, state: 'done' as const }));
  const { container: done } = render(<Stepper steps={allDone} />);
  expect(fills(done)[1]).toContain('bg-success');
});

test('the outer halves are kept but invisible, so the rail starts and ends under a dot', () => {
  const { container } = render(<Stepper steps={STEPS} />);
  const halves = [...container.querySelectorAll('li [aria-hidden="true"] > span')];
  expect(halves[0]?.className).toContain('invisible');
  expect(halves.at(-1)?.className).toContain('invisible');
  expect(halves[1]?.className).not.toContain('invisible');
});

test('the current dot breathes only when asked to', () => {
  const { container, rerender } = render(<Stepper steps={STEPS} />);
  expect(container.querySelector('.tb-livedot')).toBeNull();
  rerender(<Stepper steps={STEPS} pulse />);
  expect(container.querySelector('.tb-livedot')).not.toBeNull();
});

test('numbered steps show their number until they are done', () => {
  render(
    <Stepper
      numbered
      steps={[
        { id: '1', state: 'done' },
        { id: '2', state: 'current' },
        { id: '3', state: 'upcoming' },
      ]}
    />,
  );
  const items = screen.getAllByRole('listitem');
  expect(items[0]).not.toHaveTextContent('1');
  expect(items[1]).toHaveTextContent('2');
  expect(items[2]).toHaveTextContent('3');
});

test('`current` narrows to one line naming the current step', () => {
  const { container } = render(<Stepper steps={STEPS} narrow="current" />);
  const line = container.querySelector('.md\\:hidden');
  expect(line).toHaveTextContent('Чтение');

  const { container: idle } = render(
    <Stepper steps={STEPS.map((step) => ({ ...step, state: 'upcoming' }))} narrow="current" />,
  );
  expect(idle.querySelector('.md\\:hidden')).toBeNull();
});

test('a caption sits under its label', () => {
  render(
    <Stepper
      narrow="list"
      steps={[{ id: 'a', label: 'Цели', caption: '2 цели', state: 'done' }]}
    />,
  );
  expect(screen.getByText('2 цели').previousSibling).toHaveTextContent('Цели');
});
