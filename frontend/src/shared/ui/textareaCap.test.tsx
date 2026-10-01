import { fireEvent, render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';
import { Textarea } from './Input';

test('capped composers scroll long messages and shrink after deletion', () => {
  render(
    <Textarea
      aria-label="Message"
      variant="composer"
      maxRows={6}
      style={{ lineHeight: '20px', paddingTop: '8px', paddingBottom: '8px' }}
    />,
  );
  const area = screen.getByRole<HTMLTextAreaElement>('textbox');
  Object.defineProperty(area, 'scrollHeight', { configurable: true, value: 216 });
  fireEvent.input(area);
  expect(area.style.height).toBe('136px');
  expect(area.style.overflowY).toBe('auto');
  Object.defineProperty(area, 'scrollHeight', { configurable: true, value: 36 });
  fireEvent.input(area);
  expect(area.style.height).toBe('36px');
  expect(area.style.overflowY).toBe('hidden');
});
