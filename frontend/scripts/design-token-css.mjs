import { readFileSync, readdirSync } from 'node:fs';
import { resolve, relative } from 'node:path';
import postcss from 'postcss';

const source = resolve('src');
const units = /(-?(?:\d+(?:\.\d*)?|\.\d+))(?:px|rem|em|vw|vh|vmin|vmax|ch|ex|lh|rlh|ms|s)\b/gi;
const rawColor = /#(?:[\da-f]{3,4}|[\da-f]{6}|[\da-f]{8})\b|(?:rgb|hsl|oklch)a?\(/i;
const numericTypography = new Set(['line-height', 'font-weight', 'letter-spacing']);

function cssFiles(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = resolve(directory, entry.name);
    if (entry.isDirectory()) return cssFiles(path);
    return path.endsWith('.css') ? [path] : [];
  });
}

function hasException(node) {
  const siblings = node.parent.nodes;
  const previous = siblings[siblings.indexOf(node) - 1];
  return (
    previous?.type === 'comment' && previous.text.trimStart().startsWith('design-token-exception:')
  );
}

function checkCss(file) {
  const css = readFileSync(file, 'utf8');
  const root = postcss.parse(css, { from: file });
  const errors = [];

  root.walkDecls((decl) => {
    const value = decl.value;
    const hasRawLength = [...value.matchAll(units)].some(([, number]) => Number(number) !== 0);
    const hasRawColor = rawColor.test(value) && !/theme\(/i.test(value);
    const hasRawTypography =
      numericTypography.has(decl.prop) && /^-?(?:\d+(?:\.\d*)?|\.\d+)$/.test(value.trim());

    if ((hasRawLength || hasRawColor || hasRawTypography) && !hasException(decl)) {
      errors.push(
        `${relative(process.cwd(), file)}:${decl.source.start.line}: ${decl.prop}: ${value}`,
      );
    }
  });

  root.walkComments((comment) => {
    const siblings = comment.parent.nodes;
    const next = siblings[siblings.indexOf(comment) + 1];
    if (comment.text.trimStart().startsWith('design-token-exception:') && next?.type !== 'decl') {
      errors.push(
        `${relative(process.cwd(), file)}:${comment.source.start.line}: exception must precede one declaration`,
      );
    }
  });
  return errors;
}

const errors = cssFiles(source).flatMap(checkCss);
if (errors.length > 0) {
  console.error(errors.join('\n'));
  process.exitCode = 1;
} else {
  console.log('CSS design values: tokens or reasoned one-off exceptions');
}
