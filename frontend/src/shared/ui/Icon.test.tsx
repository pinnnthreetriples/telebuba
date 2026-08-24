import { render } from '@testing-library/react';
import { expect, test } from 'vitest';

import { Icon } from './Icon';

test('a name draws the shape the registry holds for it', () => {
  const { container } = render(<Icon name="check" size={16} />);

  expect(container.querySelector('path')).toHaveAttribute('d', 'M20 6 9 17l-5-5');
  expect(container.querySelector('svg')).toHaveAttribute('viewBox', '0 0 24 24');
});

// A registry entry may be several elements, and the ones built from circles and
// rects have to survive the trip as circles and rects.
test('an icon of several parts draws all of them', () => {
  const { container } = render(<Icon name="eye" size={16} />);

  expect(container.querySelectorAll('path')).toHaveLength(1);
  expect(container.querySelector('circle')).toHaveAttribute('r', '3');
});

test('the size sets the box', () => {
  const { container } = render(<Icon name="check" size={12} />);
  const svg = container.querySelector('svg');

  expect(svg).toHaveAttribute('width', '12');
  expect(svg).toHaveAttribute('height', '12');
});

// The rule the component exists for: a stroke width is in viewBox units, so the
// same number renders thinner as the box shrinks. Every rung has to land on the
// same 1.3 CSS px, which is 2 at 16px and 3.1 at 10px.
test('the size derives the stroke weight, so every rung draws the same line', () => {
  for (const size of [10, 12, 14, 16, 18, 20] as const) {
    const { container } = render(<Icon name="check" size={size} />);
    const stroke = Number(container.querySelector('svg')?.getAttribute('stroke-width'));

    expect((stroke * size) / 24).toBeCloseTo(1.3, 1);
  }

  const { container } = render(<Icon name="check" size={16} />);
  expect(container.querySelector('svg')).toHaveAttribute('stroke-width', '2');
});

test('a caller class reaches the element', () => {
  const { container } = render(<Icon name="trash" size={16} className="stroke-success" />);

  expect(container.querySelector('svg')).toHaveClass('stroke-success');
});

// A solid silhouette painted with a stroke as well gets a fattened, blurred edge.
test('a fill-only icon is filled and carries no stroke', () => {
  const { container } = render(<Icon name="play" size={16} />);
  const svg = container.querySelector('svg');

  expect(svg).toHaveAttribute('fill', 'currentColor');
  expect(svg).not.toHaveAttribute('stroke');
  expect(svg).not.toHaveAttribute('stroke-width');
});
