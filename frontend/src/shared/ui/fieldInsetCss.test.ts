import postcss from 'postcss';
import tailwind from 'tailwindcss';
import { expect, test } from 'vitest';

import config from '../../../tailwind.config';

async function trailingPadding(controlHeight = '36px'): Promise<string | undefined> {
  const result = await postcss([
    tailwind({
      ...config,
      theme: { ...config.theme, height: { ...config.theme.height, control: controlHeight } },
      content: [{ raw: 'px-md field-end-inset', extension: 'html' }],
    }),
  ]).process('@tailwind components; @tailwind utilities;', { from: undefined });
  let effective: string | undefined;
  result.root.walkRules((rule) => {
    if (rule.selector !== '.px-md' && rule.selector !== '.field-end-inset') return;
    rule.walkDecls('padding-right', (declaration) => {
      effective = declaration.value;
    });
  });
  return effective;
}

test('trailing icon clearance wins over the input horizontal padding in emitted CSS', async () => {
  expect(await trailingPadding()).toBe('36px');
});

test('trailing icon clearance follows the control height token through emitted CSS', async () => {
  expect(await trailingPadding('44px')).toBe('44px');
});
