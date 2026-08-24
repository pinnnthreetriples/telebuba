import { render } from '@testing-library/react';
import { expect, test } from 'vitest';

import { StatusIcon } from './StatusIcon';

test('renders a checkmark path for ok', () => {
  const { container } = render(<StatusIcon kind="ok" />);
  expect(container.querySelector('path')).toHaveAttribute('d', 'M20 6 9 17l-5-5');
});

// Lucide spells the cross as two paths where the hand-copied registry spelled it as
// one two-stroke path. Same rendered cross, so the assertion follows the markup.
test('renders a cross path for err', () => {
  const { container } = render(<StatusIcon kind="err" />);
  const strokes = [...container.querySelectorAll('path')].map((path) => path.getAttribute('d'));

  expect(strokes).toEqual(['M18 6 6 18', 'm6 6 12 12']);
});
