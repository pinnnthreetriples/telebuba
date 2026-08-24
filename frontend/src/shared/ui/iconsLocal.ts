// The shapes Lucide does not have. Every other glyph <Icon> draws now comes from the
// library, so this file is an exception list and nothing else: a shape earns a place
// here only after a search of all four thousand Lucide icons comes back empty.
//
// Built with `createLucideIcon` rather than hand-rolled JSX so a local shape takes the
// library's own defaults — 24-unit box, round caps and joins, `size` and `strokeWidth`
// as props — and `Icon` needs no branch to tell the two kinds apart.
import { createLucideIcon } from 'lucide-react';

// A square with a zipper down the middle: the marker the account importer draws beside
// a .zip of tdata, where the plain `file` glyph marks a bare .session. It has never
// been an alert — `alert-square` is only the name the two call sites already pass, and
// renaming it is a separate change from installing the library.
//
// Lucide's nearest are `file-archive`, which is a document rather than a square and so
// collides with the `file` it sits next to, and `archive`, a lidded box that reads as a
// crate at 16px. Neither says "the zipped one" beside a page.
// The `key` on each part is the React key Lucide's renderer maps with — the library's
// own icons carry a generated hash there, and a word is as unique across two parts.
export const AlertSquare = createLucideIcon('alert-square', [
  ['rect', { x: '3', y: '3', width: '18', height: '18', rx: '2', key: 'box' }],
  ['path', { d: 'M12 7v2M12 12v2M12 17v.5', key: 'zip' }],
]);
