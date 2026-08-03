import { render, screen } from '@testing-library/react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { ProxyDeleteModal } from './ProxyDeleteModal';

// The dialog's accessible name has to be the same sentence as the visible title.
// Without the interpolation a screen reader announced the raw template,
// "Удалить прокси {{endpoint}}?", while the heading read the real endpoint.
test('the dialog is announced with the endpoint, like its visible title', () => {
  render(
    <ProxyDeleteModal endpoint="1.2.3.4:1080" used={0} onClose={vi.fn()} onConfirm={vi.fn()} />,
  );

  const title = screen.getByRole('dialog').getAttribute('aria-label');
  expect(title).toContain('1.2.3.4:1080');
  expect(title).not.toContain('{{');
  expect(screen.getByText(String(title))).toBeInTheDocument();
});
