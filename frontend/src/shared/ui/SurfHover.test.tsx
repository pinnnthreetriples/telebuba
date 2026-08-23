import { render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';

import { SurfHover } from './SurfHover';

// The unconditional class is `group-hover:-translate-x-[…]`, so a plain substring
// check would match it too; only the un-prefixed token means "pinned open".
const PINNED = /(^|\s)-translate-x-\[var\(--shift\)\]/;

test('the surface carries the shift as a custom property, so hover/open reveal exactly the actions', () => {
  render(
    <SurfHover
      shift={144}
      surfaceId="surf"
      surface={<div>row</div>}
      actions={<button type="button">pause</button>}
    />,
  );

  const surface = document.getElementById('surf');
  expect(surface).not.toBeNull();
  expect(surface?.style.getPropertyValue('--shift')).toBe('144px');
  // Unrevealed, the actions are still rendered (they are only ever covered), so
  // they stay reachable by name for the hover/open state the caller drives.
  expect(screen.getByRole('button', { name: 'pause' })).toBeInTheDocument();
  expect(surface?.className).not.toMatch(PINNED);
});

test('open pins the reveal, so the actions are reachable without a hover', () => {
  render(<SurfHover open shift={96} surfaceId="surf" surface={<div>row</div>} actions={null} />);

  expect(document.getElementById('surf')?.className).toMatch(PINNED);
});
