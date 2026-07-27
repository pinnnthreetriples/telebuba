import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test } from 'vitest';

import { CollapsibleCard } from './CollapsibleCard';

// The whole a11y claim of the collapsible cards rests on this component: the
// `hidden` wrapper, the aria-expanded/aria-controls pair on BOTH toggles, and
// `Section` being a thin preset over it. jsdom applies none of index.css, so the
// max-height cap and the transitionend that drops it are browser territory (see
// the @starting-style note in index.css) — what is asserted here is the part
// that decides whether a keyboard or screen-reader operator can reach a control:
// the body's presence in the a11y tree and in the tab order.

function Card({ defaultOpen = false, extra = false }: { defaultOpen?: boolean; extra?: boolean }) {
  return (
    <CollapsibleCard label="Действия" defaultOpen={defaultOpen} header={<span>Действия</span>}>
      <button type="button">Удалить аккаунт</button>
      {extra ? <button type="button">Показать причину</button> : null}
    </CollapsibleCard>
  );
}

function toggles(): HTMLElement[] {
  return screen.getAllByRole('button', { name: 'Действия' });
}

test('both toggles announce the same body and its collapsed state', () => {
  render(<Card />);
  const [header, chevron] = toggles();
  const bodyId = (header as HTMLElement).getAttribute('aria-controls');

  expect(bodyId).not.toBeNull();
  expect(document.getElementById(bodyId as string)).toBeInTheDocument();
  for (const toggle of [header, chevron]) {
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(toggle).toHaveAttribute('aria-controls', bodyId);
  }
});

test('a collapsed body is display:none, so out of the a11y tree', () => {
  render(<Card />);

  // The node IS in the DOM — it is `hidden` that takes it out. A body that were
  // only visually collapsed (max-height:0 + opacity:0, which is all the CSS
  // does) would still answer getByRole and still take a Tab stop.
  expect(screen.getByText('Удалить аккаунт')).not.toBeVisible();
  expect(screen.queryByRole('button', { name: 'Удалить аккаунт' })).not.toBeInTheDocument();
  expect(
    document.getElementById(toggles()[0]?.getAttribute('aria-controls') ?? ''),
  ).toHaveAttribute('hidden');

  // Not asserted with userEvent.tab(): its tab-destination search reads the
  // element's own computed style and does not walk up to the `hidden` ancestor,
  // so it happily focuses this button — a jsdom/user-event limitation, not the
  // app's behaviour. `display: none` removing a subtree from the sequential
  // focus order is a platform guarantee, and display:none is what the two
  // assertions above pin down.
});

test('opening the card returns its controls to the a11y tree and the tab order', async () => {
  render(<Card />);
  await userEvent.click(screen.getByText('Действия'));

  const remove = screen.getByRole('button', { name: 'Удалить аккаунт' });
  expect(remove).toBeVisible();
  for (const toggle of toggles()) {
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
  }

  // The click left focus on the header toggle; chevron next, then the body.
  await userEvent.tab();
  await userEvent.tab();
  expect(remove).toHaveFocus();
});

test('a control that appears after the open is reachable, not sealed in', async () => {
  const { rerender } = render(<Card />);
  await userEvent.click(screen.getByText('Действия'));

  // An inline error footer, a growing upload list, a nested collapsible: content
  // the card did not measure at open time still has to be operable.
  rerender(<Card extra />);
  expect(screen.getByRole('button', { name: 'Показать причину' })).toBeVisible();
  expect(screen.getByRole('button', { name: 'Удалить аккаунт' })).toBeVisible();
});

test('a card asked to start open needs no click', () => {
  render(<Card defaultOpen />);

  expect(screen.getByRole('button', { name: 'Удалить аккаунт' })).toBeVisible();
  for (const toggle of toggles()) {
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
  }
});
