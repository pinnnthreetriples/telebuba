import { render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';

import { TerminalPane } from './TerminalPane';

test('feed is the default and inline is shorter with a smaller radius', () => {
  const { rerender } = render(<TerminalPane>строка</TerminalPane>);
  const pane = screen.getByText('строка');
  expect(pane.className).toContain('max-h-feed');
  expect(pane.className).toContain('leading-log');
  expect(pane.className).toContain('py-sm');

  rerender(<TerminalPane size="inline">строка</TerminalPane>);
  expect(screen.getByText('строка').className).toContain('max-h-feedInline');
  expect(screen.getByText('строка').className).toContain('rounded-md');
});
