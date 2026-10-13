import { describe, expect, it } from 'vitest';

import { estimateRegistrationYear } from './registrationEstimate';

describe('estimateRegistrationYear', () => {
  it('brackets the id between public anchors', () => {
    expect(estimateRegistrationYear(5e7)).toBe(2014);
    expect(estimateRegistrationYear(1.2e9)).toBe(2020);
    expect(estimateRegistrationYear(6.5e9)).toBe(2024);
  });

  it('caps a newer id at the last anchor and knows nothing without one', () => {
    expect(estimateRegistrationYear(9e9)).toBe(2025);
    expect(estimateRegistrationYear(null)).toBeNull();
    expect(estimateRegistrationYear(0)).toBeNull();
  });
});
