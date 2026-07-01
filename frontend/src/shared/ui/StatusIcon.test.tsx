import { render } from '@testing-library/react';
import { expect, test } from 'vitest';

import { StatusIcon } from './StatusIcon';

test('renders a checkmark path for ok', () => {
  const { container } = render(<StatusIcon kind="ok" />);
  expect(container.querySelector('path')).toHaveAttribute('d', 'M20 6 9 17l-5-5');
});

test('renders a cross path for err', () => {
  const { container } = render(<StatusIcon kind="err" />);
  expect(container.querySelector('path')).toHaveAttribute('d', 'M18 6 6 18M6 6l12 12');
});
