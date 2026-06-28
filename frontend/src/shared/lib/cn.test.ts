import { describe, expect, it } from 'vitest';

import { cn } from './cn';

describe('cn', () => {
  it('joins truthy class names and drops falsy ones', () => {
    expect(cn('a', false, 'b', undefined, null, 'c')).toBe('a b c');
  });

  it('lets a later Tailwind utility win over an earlier conflicting one', () => {
    expect(cn('px-2 px-4')).toBe('px-4');
  });
});
