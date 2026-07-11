import { render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';

import { HelpHint } from './HelpHint';

test('renders the "?" badge with the hint text and example', () => {
  render(<HelpHint text="Explains the field" example="Example: 3" />);

  const badge = screen.getByRole('note');
  expect(badge).toHaveTextContent('?');
  // full text (text + example) is the accessible/native title
  expect(badge).toHaveAttribute('title', 'Explains the field\nExample: 3');
  // both the explanation and the example render in the tooltip
  expect(screen.getByRole('tooltip')).toHaveTextContent('Explains the field');
  expect(screen.getByText('Example: 3')).toBeInTheDocument();
});

test('omits the example line when no example is given', () => {
  render(<HelpHint text="Just the explanation" />);

  expect(screen.getByRole('note')).toHaveAttribute('title', 'Just the explanation');
  expect(screen.getByRole('tooltip')).toHaveTextContent('Just the explanation');
});

test('the badge is keyboard-focusable so the hint opens on focus', () => {
  render(<HelpHint text="focusable" />);

  expect(screen.getByRole('note')).toHaveAttribute('tabindex', '0');
});
