// Builds preview.html: every glyph in svg/ at 16, 20 and 24px, inlined so the page
// opens straight from disk. Stroke follows the app's rule (1.3px at every size).
import { readdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const names = readdirSync(join(here, 'svg')).filter((file) => file.endsWith('.svg')).sort();
const glyph = (file, size) => readFileSync(join(here, 'svg', file), 'utf8')
  .replace(/width="24" height="24"/, `width="${size}" height="${size}"`)
  .replace(/stroke-width="[\d.]+"/, `stroke-width="${Math.round((1.3 * 24 / size) * 10) / 10}"`);

const cells = names.map((file) => `
  <figure>
    <div class="row">${[16, 20, 24].map((size) => glyph(file, size)).join('')}</div>
    <div class="big">${glyph(file, 48)}</div>
    <figcaption>${file.replace('.svg', '')}</figcaption>
  </figure>`).join('');

writeFileSync(join(here, 'preview.html'), `<!doctype html>
<meta charset="utf-8"><title>Telebuba icons</title>
<style>
  body { margin: 0; padding: 32px; font: 13px/1.4 Inter, system-ui, sans-serif; background: #f1efed; color: #0b0b0c; }
  h1 { font-size: 20px; font-weight: 600; margin: 0 0 4px; }
  p { margin: 0 0 24px; color: #63615d; }
  main { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 12px; }
  figure { margin: 0; background: #fff; border: 1px solid #e6e5e3; border-radius: 12px; padding: 16px; display: grid; gap: 12px; justify-items: center; }
  .row { display: flex; align-items: center; gap: 12px; color: #0b0b0c; }
  .big { color: #0066ff; position: relative; background-image: linear-gradient(#e6e5e3 1px, transparent 1px), linear-gradient(90deg, #e6e5e3 1px, transparent 1px); background-size: 4px 4px; }
  .big svg { display: block; }
  figcaption { color: #63615d; font-size: 12px; }
</style>
<h1>Telebuba icons</h1>
<p>${names.length} glyphs · 16 / 20 / 24 px, stroke 1.3px · 48px on a 2-unit grid</p>
<main>${cells}</main>
`);
