import { act, render, screen } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';

import { Odometer } from './Odometer';

afterEach(() => vi.restoreAllMocks());

function columns(container: HTMLElement) {
  return [...container.querySelectorAll<HTMLElement>('[style*="translateY"]')].map(
    (column) => column.style.transform,
  );
}

test('rolls from zero after the first frame, then follows live values', () => {
  const frames: FrameRequestCallback[] = [];
  vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => {
    frames.push(callback);
    return frames.length;
  });

  const { container, rerender } = render(<Odometer value={24} className="text-action-primary" />);
  expect(columns(container)).toEqual(['translateY(0.00em)', 'translateY(0.00em)']);
  expect(screen.getByText('24')).toHaveClass('sr-only');

  act(() => frames.shift()?.(0));
  expect(columns(container)).toEqual(['translateY(0.00em)', 'translateY(0.00em)']);

  act(() => frames.shift()?.(16));
  expect(columns(container)).toEqual(['translateY(-2.20em)', 'translateY(-4.40em)']);

  rerender(<Odometer value={35} className="text-action-primary" />);
  expect(columns(container)).toEqual(['translateY(-3.30em)', 'translateY(-5.50em)']);
});

test('shows the final number immediately when reduced motion is requested', () => {
  vi.spyOn(window, 'matchMedia').mockReturnValue({ matches: true } as MediaQueryList);
  const frame = vi.spyOn(window, 'requestAnimationFrame');

  const { container } = render(<Odometer value={14} />);
  expect(columns(container)).toEqual(['translateY(-1.10em)', 'translateY(-4.40em)']);
  expect(frame).not.toHaveBeenCalled();
});

test('a new digit restarts the roll and grows the clip instead of reversing the old column', () => {
  const frames: FrameRequestCallback[] = [];
  vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => {
    frames.push(callback);
    return frames.length;
  });

  const { container, rerender } = render(<Odometer value={9} />);
  act(() => frames.shift()?.(0));
  act(() => frames.shift()?.(16));
  expect(columns(container)).toEqual(['translateY(-9.90em)']);

  rerender(<Odometer value={10} />);
  expect(columns(container)).toEqual(['translateY(0.00em)', 'translateY(0.00em)']);
  expect(container.firstElementChild).toHaveStyle({ width: '2ch' });

  act(() => frames.shift()?.(32));
  act(() => frames.shift()?.(48));
  expect(columns(container)).toEqual(['translateY(-1.10em)', 'translateY(0.00em)']);
});
