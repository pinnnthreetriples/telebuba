import { ENTITY_ORDER, normalizeEntities } from './entities';
import type { CaptionValue, StoryEntity, StoryEntityType } from './types';

// The bridge between the editor's DOM and `{text, entities}`.
//
// Text lives in the DOM as plain text with real "\n" characters (the editor is
// `white-space: pre-wrap`); marks are nested elements. Browsers sometimes bring their
// own structure anyway — `<br>`, a `<div>` per line after an IME or a drop — so reading
// accepts those too. Reading and measuring a selection share ONE walk, so a caret offset
// can never disagree with the text it is an offset into.

const SENTINEL = 'data-sentinel';
const URL_ATTR = 'data-url';
const SPOILER_ATTR = 'data-spoiler';
const BLOCK = new Set(['DIV', 'P']);

interface Mark {
  type: StoryEntityType;
  url?: string;
}

function markOf(el: Element): Mark | null {
  switch (el.tagName) {
    case 'STRONG':
    case 'B':
      return { type: 'bold' };
    case 'EM':
    case 'I':
      return { type: 'italic' };
    case 'U':
      return { type: 'underline' };
    case 'S':
    case 'STRIKE':
    case 'DEL':
      return { type: 'strike' };
    case 'CODE':
      return { type: 'code' };
    case 'A':
      return { type: 'text_url', url: el.getAttribute(URL_ATTR) ?? el.getAttribute('href') ?? '' };
    default:
      return el.hasAttribute(SPOILER_ATTR) ? { type: 'spoiler' } : null;
  }
}

interface Target {
  node: Node;
  offset: number;
}

interface Scan {
  text: string;
  entities: StoryEntity[];
  found: number | null;
}

// Marks open across consecutive segments are extended instead of repeated, so
// `<b>ab</b><b>cd</b>` is one entity — the renderer splits text at every boundary.
function addSegment(
  scan: Scan,
  piece: string,
  marks: readonly Mark[],
  open: Map<string, StoryEntity>,
) {
  if (piece === '') return;
  const start = scan.text.length;
  scan.text += piece;
  for (const mark of marks) {
    if (mark.type === 'text_url' && !mark.url) continue;
    const key = `${mark.type}|${mark.url ?? ''}`;
    const current = open.get(key);
    if (current && current.offset + current.length === start) {
      current.length += piece.length;
    } else {
      const entity: StoryEntity = { type: mark.type, offset: start, length: piece.length };
      if (mark.url !== undefined) entity.url = mark.url;
      scan.entities.push(entity);
      open.set(key, entity);
    }
  }
}

function walk(
  node: Node,
  marks: readonly Mark[],
  scan: Scan,
  open: Map<string, StoryEntity>,
  target: Target | null,
) {
  const hit = (offset: number) => {
    if (target && target.node === node && target.offset === offset && scan.found === null) {
      scan.found = scan.text.length;
    }
  };
  if (node.nodeType === 3) {
    const data = node.textContent ?? '';
    if (target && target.node === node && scan.found === null) {
      scan.found = scan.text.length + Math.min(target.offset, data.length);
    }
    addSegment(scan, data, marks, open);
    return;
  }
  if (!(node instanceof Element)) return;
  if (node.tagName === 'BR') {
    if (!node.hasAttribute(SENTINEL)) addSegment(scan, '\n', marks, open);
    return;
  }
  let inner = marks;
  const mark = markOf(node);
  if (mark) inner = [...marks, mark];
  if (BLOCK.has(node.tagName) && scan.text !== '' && !scan.text.endsWith('\n')) {
    addSegment(scan, '\n', [], open);
  }
  const children = [...node.childNodes];
  children.forEach((child, index) => {
    hit(index);
    walk(child, inner, scan, open, target);
  });
  hit(children.length);
}

function scanRoot(root: Node, target: Target | null): Scan {
  const scan: Scan = { text: '', entities: [], found: null };
  walk(root, [], scan, new Map(), target);
  return scan;
}

export function readCaption(root: Node): CaptionValue {
  const { text, entities } = scanRoot(root, null);
  return { text, entities: normalizeEntities(entities, text.length) };
}

// The text offset of a DOM point, or null when the point is outside `root`.
export function offsetOfPoint(root: Node, node: Node, offset: number): number | null {
  if (!root.contains(node)) return null;
  return scanRoot(root, { node, offset }).found;
}

// The DOM point for a text offset, preferring the end of the earlier text node so a
// caret at a mark's edge stays where the user left it.
export function pointAtOffset(root: Node, offset: number): Target {
  let remaining = offset;
  let last: Target = { node: root, offset: root.childNodes.length };
  const visit = (node: Node): Target | null => {
    if (node.nodeType === 3) {
      const length = (node.textContent ?? '').length;
      if (remaining <= length) return { node, offset: remaining };
      remaining -= length;
      last = { node, offset: length };
      return null;
    }
    if (node instanceof Element && node.tagName === 'BR') {
      if (node.hasAttribute(SENTINEL)) return null;
      if (remaining === 0) {
        const parent = node.parentNode;
        return parent
          ? { node: parent, offset: [...parent.childNodes].indexOf(node as ChildNode) }
          : null;
      }
      remaining -= 1;
      return null;
    }
    for (const child of node.childNodes) {
      const found = visit(child);
      if (found) return found;
    }
    return null;
  };
  return visit(root) ?? last;
}

interface Piece {
  text: string;
  marks: Mark[];
}

// Split at every entity boundary; each piece carries the marks active over it.
function pieces(value: CaptionValue): Piece[] {
  const cuts = new Set<number>([0, value.text.length]);
  for (const entity of value.entities) {
    cuts.add(Math.max(0, entity.offset));
    cuts.add(Math.min(value.text.length, entity.offset + entity.length));
  }
  const points = [...cuts].sort((a, b) => a - b);
  const out: Piece[] = [];
  for (let i = 0; i < points.length - 1; i += 1) {
    const from = points[i] ?? 0;
    const to = points[i + 1] ?? from;
    if (to <= from) continue;
    const marks = value.entities
      .filter((entity) => entity.offset <= from && entity.offset + entity.length >= to)
      .sort((a, b) => ENTITY_ORDER.indexOf(a.type) - ENTITY_ORDER.indexOf(b.type))
      .map((entity) => ({ type: entity.type, url: entity.url }));
    out.push({ text: value.text.slice(from, to), marks });
  }
  return out;
}

// Bold, italic, underline and strike are what the browser already draws for their tags;
// the rest are painted from tokens. A spoiler is obscured rather than blurred: a filled
// plate with muted ink, still legible enough to edit.
const MARK_CLASS: Partial<Record<StoryEntityType, string>> = {
  code: 'rounded-sm bg-canvas px-1 font-mono',
  spoiler: 'rounded-sm bg-content-subtle text-on-fill',
  text_url: 'text-info-strong underline',
};

const MARK_TAG: Record<StoryEntityType, string> = {
  bold: 'strong',
  italic: 'em',
  underline: 'u',
  strike: 's',
  code: 'code',
  spoiler: 'span',
  text_url: 'a',
};

function elementFor(mark: Mark): HTMLElement {
  const el = document.createElement(MARK_TAG[mark.type]);
  const className = MARK_CLASS[mark.type];
  if (className) el.className = className;
  if (mark.type === 'spoiler') el.setAttribute(SPOILER_ATTR, '');
  if (mark.type === 'text_url') el.setAttribute(URL_ATTR, mark.url ?? '');
  return el;
}

// Rebuild the editor's DOM from a value. A trailing "\n" gets a sentinel `<br>` because
// a browser will not show an empty last line otherwise; reading ignores the sentinel.
export function renderCaption(root: HTMLElement, value: CaptionValue): void {
  root.replaceChildren();
  for (const piece of pieces(value)) {
    let outer: Node = document.createTextNode(piece.text);
    for (const mark of [...piece.marks].reverse()) {
      const wrapper = elementFor(mark);
      wrapper.appendChild(outer);
      outer = wrapper;
    }
    root.appendChild(outer);
  }
  if (value.text.endsWith('\n')) {
    const br = document.createElement('br');
    br.setAttribute(SENTINEL, '');
    root.appendChild(br);
  }
}

export function sameCaption(a: CaptionValue, b: CaptionValue): boolean {
  return (
    a.text === b.text &&
    a.entities.length === b.entities.length &&
    a.entities.every((entity, index) => {
      const other = b.entities[index];
      return (
        other !== undefined &&
        other.type === entity.type &&
        other.offset === entity.offset &&
        other.length === entity.length &&
        (other.url ?? '') === (entity.url ?? '')
      );
    })
  );
}
