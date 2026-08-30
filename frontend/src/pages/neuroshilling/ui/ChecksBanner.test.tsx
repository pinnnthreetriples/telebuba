import { render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';

import '@/shared/i18n';

import { ChecksBanner } from './ChecksBanner';

test('every blocking reason is listed, not just the first', () => {
  // The point of the list: fixing one reason only to be refused by the next is the
  // loop it exists to end. It moved here from the launch card, and it must not have
  // become «the first one and a number» on the way.
  render(<ChecksBanner blockers={['Сценарий не утверждён', 'Нет сохранённых целей']} />);
  expect(screen.getByText('Сценарий не утверждён', { exact: false })).toBeInTheDocument();
  expect(screen.getByText('Нет сохранённых целей', { exact: false })).toBeInTheDocument();
});

test('the count is declined, so one reason does not read as «1 замечаний»', () => {
  render(<ChecksBanner blockers={['Нет сохранённых целей']} />);
  expect(screen.getAllByText('1 замечание').length).toBeGreaterThan(0);
});

test('the list is open on arrival rather than folded behind a click', () => {
  // Collapsing is offered, but the reasons were visible on the old launch card and
  // must stay visible: a banner that only says «3» sends the operator looking.
  render(<ChecksBanner blockers={['Нет сохранённых целей']} />);
  expect(screen.getByText('· Нет сохранённых целей')).toBeVisible();
});

test('nothing is drawn when nothing is wrong', () => {
  // Readiness is already stated twice — by the pipeline's green notice and by the
  // enabled Start button. A third «всё хорошо» would be a permanent empty box.
  const { container } = render(<ChecksBanner blockers={[]} />);
  expect(container).toBeEmptyDOMElement();
});
