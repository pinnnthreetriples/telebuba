import { render } from '@testing-library/react';
import { expect, test } from 'vitest';

import { expectNoAxeViolations } from './axe.test-helpers';
import { Icon } from './Icon';

test('a name draws the library glyph it is mapped to', async () => {
  const { container } = render(<Icon name="check" size={16} />);

  expect(container.querySelector('path')).toHaveAttribute('d', 'M20 6 9 17l-5-5');
  expect(container.querySelector('svg')).toHaveAttribute('viewBox', '0 0 24 24');
  await expectNoAxeViolations(container);
});

// The table is the whole reason this component still exists: the app's vocabulary and
// Lucide's disagree, and `chart` → `audio-lines` is the pair furthest apart.
test('a name Lucide spells differently still finds its glyph', () => {
  const { container } = render(<Icon name="chart" size={16} />);

  expect(container.querySelectorAll('path')).toHaveLength(6);
  expect(container.querySelector('path')).toHaveAttribute('d', 'M2 10v3');
});

// The one shape Lucide has no glyph for. If the library ever grows a square with a
// zipper this test is what says the local copy can go.
test('a shape the library lacks is drawn from the local file', () => {
  const { container } = render(<Icon name="alert-square" size={16} />);

  expect(container.querySelector('rect')).toHaveAttribute('width', '18');
  expect(container.querySelector('path')).toHaveAttribute('d', 'M12 7v2M12 12v2M12 17v.5');
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
// Lucide writes `stroke-width` unconditionally, so the stroke is turned off by paint
// rather than by absence — `stroke="none"` draws nothing whatever the width says.
test('a fill-only icon is filled and paints no stroke', () => {
  const { container } = render(<Icon name="play" size={16} />);
  const svg = container.querySelector('svg');

  expect(svg).toHaveAttribute('fill', 'currentColor');
  expect(svg).toHaveAttribute('stroke', 'none');
});
