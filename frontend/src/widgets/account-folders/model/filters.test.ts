import { expect, test } from 'vitest';

import { activeFilterKeys, countryName, listQuery, NO_FILTERS } from './filters';

test('the full list with no filters asks for every status and nothing else', () => {
  expect(listQuery('all', NO_FILTERS)).toEqual({ status: 'all' });
});

test('a folder tab and every filter go over as the list endpoint params', () => {
  expect(listQuery('f-1', { phone: 49, proxy: 'DE', status: 'needs_code', minTrust: 70 })).toEqual({
    status: 'needs_code',
    folder: 'f-1',
    phone_code: 49,
    proxy_country: 'DE',
    min_trust: 70,
  });
  expect(listQuery('unfiled', { ...NO_FILTERS, proxy: 'none' })).toEqual({
    status: 'all',
    folder: 'unfiled',
    proxy_country: 'none',
  });
});

test('only the filters that are set count as active, in display order', () => {
  expect(activeFilterKeys(NO_FILTERS)).toEqual([]);
  expect(activeFilterKeys({ ...NO_FILTERS, minTrust: 50, phone: 7 })).toEqual([
    'phone',
    'minTrust',
  ]);
});

test('a country is named in the UI language, else by the given name, else by its code', () => {
  expect(countryName('de', 'ru')).toBe('Германия');
  expect(countryName('DE', 'en')).toBe('Germany');
  expect(countryName('not a code', 'ru', 'Somewhere')).toBe('Somewhere');
  expect(countryName('not a code', 'ru')).toBe('NOT A CODE');
});
