import { render, screen } from '@testing-library/react';
import { expect, test, vi } from 'vitest';

import { CloseButton } from './CloseButton';

test('uses a centered circular icon button with its accessible name', () => {
  render(<CloseButton aria-label="Закрыть" onClick={vi.fn()} />);

  expect(screen.getByRole('button', { name: 'Закрыть' })).toHaveClass(
    'size-icon',
    'rounded-full',
    'items-center',
    'justify-center',
  );
});
