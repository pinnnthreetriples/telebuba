import { readFile, writeFile } from 'node:fs/promises';

import { expect, test, type Page } from '@playwright/test';
import ts from 'typescript';

const componentsPath = new URL('../src/shared/design-system/tokens/components.ts', import.meta.url);
const layoutPath = new URL('../src/shared/design-system/tokens/layout.ts', import.meta.url);

// Change the actual source setting, not a story prop or a browser-only style.
function setting(source: string, keys: string[], value: string): string {
  const parsed = ts.createSourceFile('settings.ts', source, ts.ScriptTarget.Latest, true);
  let node: ts.Node | undefined;
  parsed.forEachChild((statement) => {
    if (!ts.isVariableStatement(statement)) return;
    const declaration = statement.declarationList.declarations.find(
      (entry) => ts.isIdentifier(entry.name) && entry.name.text === keys[0],
    );
    if (declaration) node = declaration.initializer;
  });
  const unwrap = (value: ts.Node | undefined): ts.Node | undefined => {
    while (value && (ts.isAsExpression(value) || ts.isSatisfiesExpression(value)))
      value = value.expression;
    return value;
  };
  for (const key of keys.slice(1)) {
    node = unwrap(node);
    if (!node || !ts.isObjectLiteralExpression(node)) throw new Error(`Missing ${keys.join('.')}`);
    const property = node.properties.find(
      (entry) => ts.isPropertyAssignment(entry) && entry.name.getText(parsed) === key,
    );
    node = property && ts.isPropertyAssignment(property) ? property.initializer : undefined;
  }
  node = unwrap(node);
  if (!node || !ts.isStringLiteral(node))
    throw new Error(`Not a token reference: ${keys.join('.')}`);
  return source.slice(0, node.getStart(parsed)) + `'${value}'` + source.slice(node.end);
}

async function geometry(page: Page) {
  return page.evaluate(() => {
    const read = (selector: string) => {
      const element = document.querySelector(selector);
      if (!element) throw new Error(`Missing propagation consumer: ${selector}`);
      const css = getComputedStyle(element);
      return {
        padding: parseFloat(css.paddingLeft),
        height: parseFloat(css.height),
        labelGap: parseFloat(css.marginBottom),
        sectionGap: parseFloat(css.gap),
      };
    };
    return {
      cards: [1, 2].map((index) => read(`[data-testid="propagation-card-${index}"]`).padding),
      inputs: [1, 2].map((index) => read(`[data-testid="propagation-input-${index}"]`).height),
      buttons: [1, 2].map((index) => read(`[data-testid="propagation-button-${index}"]`).height),
      labels: [1, 2].map(
        (index) => read(`[data-testid="propagation-card-${index}"] label > span`).labelGap,
      ),
      section: read('[data-testid="propagation-stack"]').sectionGap,
    };
  });
}

test('saved shared settings propagate through HMR and restore exactly', async ({
  page,
}, testInfo) => {
  test.setTimeout(120_000);
  const unexpected: string[] = [];
  await page.route('**/api/v1/**', (route) => {
    unexpected.push(route.request().url());
    return route.abort();
  });
  await page.goto(
    '/iframe.html?id=design-system-components-managed-geometry--propagation&viewMode=story',
  );
  await expect(page.getByTestId('propagation-input-1')).toBeVisible();
  const originalGeometry = await geometry(page);
  const originalComponents = await readFile(componentsPath, 'utf8');
  const originalLayout = await readFile(layoutPath, 'utf8');
  let changedComponents = setting(
    originalComponents,
    ['componentSettings', 'card', 'padding', 'default', 'x'],
    '2xl',
  );
  changedComponents = setting(
    changedComponents,
    ['componentSettings', 'controls', 'height', 'md'],
    'touch',
  );
  changedComponents = setting(changedComponents, ['componentSettings', 'form', 'labelGap'], 'lg');
  const changedLayout = setting(originalLayout, ['layoutSettings', 'sectionGap', 'default'], '2xl');
  try {
    await writeFile(componentsPath, changedComponents);
    await writeFile(layoutPath, changedLayout);
    await expect
      .poll(async () => {
        // A settings dependency can trigger a full preview reload. Poll through
        // its temporary unmounted tree as well as ordinary hot updates.
        const current = await geometry(page).catch(() => null);
        if (!current) return false;
        return (
          current.cards.every((value, index) => value > originalGeometry.cards[index]!) &&
          current.inputs.every((value, index) => value > originalGeometry.inputs[index]!) &&
          current.buttons.every((value, index) => value > originalGeometry.buttons[index]!) &&
          current.labels.every((value, index) => value > originalGeometry.labels[index]!) &&
          current.section > originalGeometry.section
        );
      })
      .toBe(true);
    const changedGeometry = await geometry(page);
    expect(changedGeometry.inputs).toEqual(changedGeometry.buttons);
    await testInfo.attach('saved-settings-geometry.json', {
      body: JSON.stringify({ originalGeometry, changedGeometry }, null, 2),
      contentType: 'application/json',
    });
    await page.screenshot({ path: testInfo.outputPath('saved-settings.png'), fullPage: true });
  } finally {
    await writeFile(componentsPath, originalComponents);
    await writeFile(layoutPath, originalLayout);
  }
  await expect.poll(() => geometry(page).catch(() => null)).toEqual(originalGeometry);
  expect(await readFile(componentsPath, 'utf8')).toBe(originalComponents);
  expect(await readFile(layoutPath, 'utf8')).toBe(originalLayout);
  expect(unexpected).toEqual([]);
});
