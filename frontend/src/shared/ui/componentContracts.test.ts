import { Linter } from 'eslint';
import tseslint from 'typescript-eslint';
import { describe, expect, test } from 'vitest';

import plugin from '../../../eslint-rules/design-tokens.js';

const noop = { create: () => ({}) };

function check(code: string): string[] {
  const linter = new Linter();
  return linter
    .verify(
      code,
      {
        files: ['**/*.tsx'],
        languageOptions: {
          parser: tseslint.parser,
          parserOptions: { ecmaFeatures: { jsx: true } },
        },
        plugins: {
          ds: {
            rules: { contracts: plugin.rules['component-contracts'] ?? noop },
          },
        },
        rules: { 'ds/contracts': 'error' },
      },
      { filename: 'src/pages/demo/ui/Demo.tsx' },
    )
    .map((message) => message.messageId ?? message.message);
}

describe('shared component contracts', () => {
  test.each([
    ['Badge', 'font-bold gap-sm border'],
    ['Notice', 'py-sm text-tiny'],
    ['TabList', 'px-xl gap-md'],
    ['SegmentedControl', 'gap-lg'],
    ['Select', 'h-touch'],
    ['SelectableCard', 'p-xl'],
    ['Switch', 'size-touch'],
    ['HelpHint', 'text-body'],
    ['Spinner', 'size-icon'],
    ['Modal', 'p-xl'],
  ])('protects the shared geometry of %s', (component, classes) => {
    expect(
      check(
        `import { ${component} } from "@/shared/ui"; const view = <${component} className="${classes}" />;`,
      ),
    ).toContain('override');
  });

  test.each([
    'import { Card } from "@/shared/ui"; const view = <Card className="p-lg" />;',
    'import { Card as Box } from "@/shared/ui"; const view = <Box className={`px-xl ${on ? "py-lg" : "py-md"}`} />;',
    'import { Card } from "@/shared/ui"; const style = "p-lg"; const view = <Card className={style} />;',
    'import { Card } from "@/shared/ui"; const style = "p-lg"; const view = <Card className={cn("flex", style)} />;',
    'import { Card } from "@/shared/ui"; const props = { className: "p-lg" }; const view = <Card {...props} />;',
    'import { CollapsibleCard } from "@/shared/ui"; const view = <CollapsibleCard headerClassName="px-lg py-lg" />;',
    'import { Button } from "@/shared/ui"; const view = <Button className="rounded-lg px-md" />;',
    'import { Input } from "@/shared/ui"; const view = <Input className="h-touch" />;',
    'import { Card } from "@/shared/ui"; const styles = { compact: "p-lg" }; const view = <Card className={styles.compact} />;',
    'import { Card } from "@/shared/ui"; import { cardSpacing as inset } from "../../../../eslint-rules/fixtures/contract-classes"; const view = <Card className={inset} />;',
    'import { Card } from "@/shared/ui"; import { panelSpacing } from "../../../../eslint-rules/fixtures/contract-barrel"; const view = <Card className={panelSpacing} />;',
    'import { Card } from "@/shared/ui"; import { cardProps } from "../../../../eslint-rules/fixtures/contract-classes"; const view = <Card {...cardProps} />;',
    'import { Card } from "@/shared/ui"; import { panelProps } from "../../../../eslint-rules/fixtures/contract-barrel"; const props = { ...panelProps }; const view = <Card {...props} />;',
    'import { Card } from "@/shared/ui"; import { styles } from "../../../../eslint-rules/fixtures/contract-classes"; const view = <Card className={styles.compact} />;',
    'import { Card } from "@/shared/ui"; import { cardProps } from "../../../../eslint-rules/fixtures/contract-classes"; const view = <Card className="flex" {...cardProps} />;',
    'import { Card } from "@/shared/ui"; import { conditionalProps } from "../../../../eslint-rules/fixtures/contract-classes"; const view = <Card {...conditionalProps} />;',
    'import { Card } from "@/shared/ui"; import { reverseConditionalProps } from "../../../../eslint-rules/fixtures/contract-classes"; const view = <Card {...reverseConditionalProps} />;',
    'import { Card } from "@/shared/ui"; const inset = { className: "p-lg" }; const props = { ...inset, title: "Title" }; const view = <Card {...props} />;',
    'import { Button } from "@/shared/ui"; const view = <Button className="bg-success font-medium text-tiny" />;',
    'import { IconButton } from "@/shared/ui"; const view = <IconButton className="md:size-icon border-transparent" />;',
    'import { Input } from "@/shared/ui"; const view = <Input className="font-mono tracking-code" />;',
    'import * as UI from "@/shared/ui"; const view = <UI.Card className="p-lg" />;',
  ])('rejects an override hidden in %s', (code) => {
    expect(check(code)).toContain('override');
  });

  test('recognizes handwritten pill geometry in a template literal', () => {
    expect(
      check(
        'const view = <button className={`inline-flex rounded-full px-md py-tight ${active ? "bg-info-tint" : "bg-surface-card"}`} />;',
      ),
    ).toContain('primitive');
  });

  test('requires common text controls while retaining native file and checkbox semantics', () => {
    expect(
      check('const view = <input type="text" className="h-control rounded-lg border px-md" />;'),
    ).toContain('primitive');
    expect(
      check('const view = <><input type="file" hidden /><input type="checkbox" /></>;'),
    ).toEqual([]);
  });

  test('accepts typed variants and surrounding layout', () => {
    expect(
      check(
        'import { Card, Button } from "@/shared/ui"; const view = <Card padding="compact" className="relative flex overflow-hidden lg:col-span-2"><Button size="sm" className="w-full sm:flex-1" /></Card>;',
      ),
    ).toEqual([]);
  });

  test.each([
    'import { Card } from "@/shared/ui"; import { styles } from "../../../../eslint-rules/fixtures/contract-classes"; const view = <Card className={styles.normal} />;',
    'import { Card } from "@/shared/ui"; import { layoutProps } from "../../../../eslint-rules/fixtures/contract-classes"; const view = <Card className={layoutProps.className} />;',
    'import { Card } from "@/shared/ui"; import { cardProps } from "../../../../eslint-rules/fixtures/contract-classes"; const view = <Card {...cardProps} className="flex" />;',
  ])('checks only the effective imported classes in %s', (code) => {
    expect(check(code)).toEqual([]);
  });

  test('resolves a barrel directory without attempting to read it as a file', () => {
    expect(
      check(
        'import { Button } from "@/shared/ui"; import { PRESS_FEEDBACK } from "@/shared/design-system"; const view = <Button className={PRESS_FEEDBACK} />;',
      ),
    ).toEqual([]);
  });

  test('a reasoned local exception is explicit and narrow', () => {
    expect(
      check(
        'import { Input } from "@/shared/ui"; // design-system-exception: text inset makes room for the account handle prefix\nconst view = <Input className="pl-page" />;',
      ),
    ).toEqual([]);
    expect(
      check(
        'import { Input } from "@/shared/ui"; // design-system-exception:\nconst view = <Input className="pl-page" />;',
      ),
    ).toContain('override');
  });
});
