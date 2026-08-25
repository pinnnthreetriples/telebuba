import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test } from 'vitest';

import { expectNoAxeViolations } from './axe.test-helpers';
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

// happy-dom drops `propertyName`: its TransitionEvent constructor ignores the
// field and so does fireEvent.transitionEnd's init, so an event fired the usual
// way arrives at the handler with propertyName === undefined — which is why every
// propertyName filter in this component was invisible to this file, and a
// transitionend that should have been discarded (or accepted) was neither. A hand
// built native Event with the property defined on it does reach React's synthetic
// event intact.
function endTransition(element: HTMLElement, propertyName: string): void {
  const event = new Event('transitionend', { bubbles: true });
  Object.defineProperty(event, 'propertyName', { value: propertyName });
  fireEvent(element, event);
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
  const { container } = render(<Card />);
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
  await expectNoAxeViolations(container);
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

// React's onTransitionEnd bubbles, and the handler keys off `propertyName === 'max-height'`
// — which any descendant can satisfy (`.tb-dd` dropdowns transition exactly that, and so
// would a sub-row animated the same way). Without the target check, a descendant's
// transition ending while the card is closed applies `hidden` to the WHOLE body, which is
// the a11y regression the tests above exist to prevent.
test('a descendant’s max-height transition does not seal the card', async () => {
  render(
    <CollapsibleCard label="Действия" defaultOpen header={<span>Действия</span>}>
      <div data-testid="inner-dropdown">
        <button type="button">Удалить аккаунт</button>
      </div>
    </CollapsibleCard>,
  );
  const body = document.getElementById(toggles()[0]?.getAttribute('aria-controls') ?? '');

  // The card is closing; 80ms later an inner dropdown finishes its own run — of
  // max-height, and of the opacity the close now keys off.
  await userEvent.click(screen.getByText('Действия'));
  endTransition(screen.getByTestId('inner-dropdown'), 'max-height');
  endTransition(screen.getByTestId('inner-dropdown'), 'opacity');
  expect(body).not.toHaveAttribute('hidden');

  // Re-opening still works, i.e. the guard did not cost the card its own
  // transitionend — and `tb-settled` is the only observable proof of that. It is
  // what the card's own max-height transitionend is FOR: it swaps the animation's
  // `--mh` cap for `max-height: none` so a body taller than the CSS fallback is not
  // left clipped. Asserting only that the button is visible passed with
  // `setSettled(true)` deleted, because visibility rides on `reachable`.
  await userEvent.click(screen.getByText('Действия'));
  expect(body).not.toHaveClass('tb-settled');
  endTransition(body as HTMLElement, 'max-height');
  expect(body).toHaveClass('tb-settled');
  expect(screen.getByRole('button', { name: 'Удалить аккаунт' })).toBeVisible();
});

// The other half of the a11y claim, and the half that shipped broken: a card that
// was opened and then closed has to go BACK out of the a11y tree. `hidden` returns
// only when the close transition ends, so what the handler accepts as "the close
// ended" is the whole mechanism.
//
// In Chrome the close never ends on max-height. `.tb-collapse.tb-open.tb-settled`
// drops the cap to `max-height: none`, and `none -> 0` is not interpolable, so no
// max-height transition is created at all — only opacity runs. A handler filtering
// on max-height therefore never fired and every collapsed card in the app handed
// its controls back to the keyboard and the screen reader after one open/close
// cycle (measured in Chrome against a verbatim copy of the CSS: `.focus()` inside
// the closed body succeeded and a nested input's value was readable).
//
// Honest caveat: happy-dom fires NO transitionend of its own and applies no
// transition CSS, so this test cannot reproduce the browser bug — it pins the
// contract (the card closes on the signal a real browser actually sends) and it
// fails against the pre-fix handler and against a handler with `setReachable(false)`
// deleted. The CSS/handler pairing in index.css is the real fix.
test('a settled card that closes leaves the a11y tree again', async () => {
  render(<Card defaultOpen />);
  const body = document.getElementById(toggles()[0]?.getAttribute('aria-controls') ?? '');

  // Settle the open first — that is what replaces the numeric cap with `none` and
  // makes the following close non-interpolable in a browser.
  endTransition(body as HTMLElement, 'max-height');
  await userEvent.click(screen.getByText('Действия'));
  expect(body).not.toHaveAttribute('hidden');

  // The close ends on opacity, because in a browser it is the only property that
  // transitions here.
  endTransition(body as HTMLElement, 'opacity');
  expect(body).toHaveAttribute('hidden');
  expect(screen.queryByRole('button', { name: 'Удалить аккаунт' })).not.toBeInTheDocument();
});

test('a card asked to start open needs no click', () => {
  render(<Card defaultOpen />);

  expect(screen.getByRole('button', { name: 'Удалить аккаунт' })).toBeVisible();
  for (const toggle of toggles()) {
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
  }
});
