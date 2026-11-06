import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { areaBase } from '@/shared/design-system';
import { cn } from '@/shared/lib/cn';

import { isCovered, linkAt, replaceRange, setLink, toggleEntity } from '../model/entities';
import { CAPTION_MAX_FREE, CAPTION_MAX_PREMIUM, MAX_ENTITIES } from '../model/limits';
import {
  offsetOfPoint,
  pointAtOffset,
  readCaption,
  renderCaption,
  sameCaption,
} from '../model/richText';
import type { CaptionValue, StoryEntityType } from '../model/types';
import { truncateUtf16 } from '../model/utf16';
import { CaptionToolbar } from './CaptionToolbar';
import { LinkPopover } from './LinkPopover';

interface Span {
  start: number;
  end: number;
}

function selectionIn(root: HTMLElement): Span | null {
  const selection = root.ownerDocument.getSelection();
  if (!selection || selection.rangeCount === 0 || !selection.anchorNode || !selection.focusNode) {
    return null;
  }
  const a = offsetOfPoint(root, selection.anchorNode, selection.anchorOffset);
  const b = offsetOfPoint(root, selection.focusNode, selection.focusOffset);
  if (a === null || b === null) return null;
  return { start: Math.min(a, b), end: Math.max(a, b) };
}

function selectIn(root: HTMLElement, span: Span): void {
  const selection = root.ownerDocument.getSelection();
  if (!selection) return;
  const from = pointAtOffset(root, span.start);
  const to = pointAtOffset(root, span.end);
  const range = root.ownerDocument.createRange();
  range.setStart(from.node, from.offset);
  range.setEnd(to.node, to.offset);
  selection.removeAllRanges();
  selection.addRange(range);
}

const MARK_KEYS: Record<string, { type: StoryEntityType; shift: boolean }> = {
  b: { type: 'bold', shift: false },
  i: { type: 'italic', shift: false },
  u: { type: 'underline', shift: false },
  x: { type: 'strike', shift: true },
  p: { type: 'spoiler', shift: true },
  m: { type: 'code', shift: true },
  k: { type: 'text_url', shift: false },
};

interface LinkDraft extends Span {
  url: string;
}

// A contenteditable caption editor over `{text, entities}`. The value is the source of
// truth but the DOM is rebuilt from it only when the incoming value differs from the
// last one this editor emitted — typing mutates the DOM natively and reports it, so the
// caret never jumps. Toolbar, shortcuts, Enter and paste edit the VALUE and re-render.
export function CaptionEditor({
  value,
  onChange,
  premium,
  disabled = false,
  label,
  placeholder,
}: {
  value: CaptionValue;
  onChange: (value: CaptionValue) => void;
  // true: 2048 is the limit. false: the counter warns past the free limit. null: unknown.
  premium: boolean | null;
  disabled?: boolean;
  label?: string;
  placeholder?: string;
}) {
  const { t } = useTranslation();
  const rootRef = useRef<HTMLDivElement>(null);
  const lastEmitted = useRef<CaptionValue>(value);
  const [active, setActive] = useState<ReadonlySet<StoryEntityType>>(new Set());
  const [hasSelection, setHasSelection] = useState(false);
  const [linkDraft, setLinkDraft] = useState<LinkDraft | null>(null);

  const refreshToolbar = useCallback(() => {
    const root = rootRef.current;
    const span = root ? selectionIn(root) : null;
    if (!root || !span || span.end <= span.start) {
      setHasSelection(false);
      setActive(new Set());
      return;
    }
    const current = readCaption(root);
    const next = new Set<StoryEntityType>();
    for (const entity of current.entities) {
      if (isCovered(current.entities, entity.type, span.start, span.end)) next.add(entity.type);
    }
    setHasSelection(true);
    setActive(next);
  }, []);

  // External change: rebuild the DOM, keeping the caret if the editor has focus.
  useLayoutEffect(() => {
    const root = rootRef.current;
    if (!root || sameCaption(value, lastEmitted.current)) return;
    const focused = root.ownerDocument.activeElement === root;
    const span = focused ? selectionIn(root) : null;
    lastEmitted.current = value;
    renderCaption(root, value);
    if (span) {
      selectIn(root, {
        start: Math.min(span.start, value.text.length),
        end: Math.min(span.end, value.text.length),
      });
    }
  }, [value]);

  // First paint.
  useLayoutEffect(() => {
    if (rootRef.current) renderCaption(rootRef.current, lastEmitted.current);
  }, []);

  useEffect(() => {
    const doc = rootRef.current?.ownerDocument;
    if (!doc) return;
    doc.addEventListener('selectionchange', refreshToolbar);
    return () => {
      doc.removeEventListener('selectionchange', refreshToolbar);
    };
  }, [refreshToolbar]);

  const commit = (next: CaptionValue, caret?: Span) => {
    const root = rootRef.current;
    if (!root) return;
    let bounded = next;
    if (next.text.length > CAPTION_MAX_PREMIUM) {
      // Cut on a code point: a lone surrogate would make the whole design unsendable.
      const keep = truncateUtf16(next.text, CAPTION_MAX_PREMIUM).length;
      bounded = replaceRange(next, keep, next.text.length, '');
    }
    lastEmitted.current = bounded;
    renderCaption(root, bounded);
    if (caret) {
      selectIn(root, {
        start: Math.min(caret.start, bounded.text.length),
        end: Math.min(caret.end, bounded.text.length),
      });
    }
    onChange(bounded);
    refreshToolbar();
  };

  const insertText = (text: string) => {
    const root = rootRef.current;
    if (!root) return;
    const span = selectionIn(root) ?? {
      start: readCaption(root).text.length,
      end: readCaption(root).text.length,
    };
    const next = replaceRange(readCaption(root), span.start, span.end, text);
    const caret = Math.min(span.start + text.length, CAPTION_MAX_PREMIUM);
    commit(next, { start: caret, end: caret });
  };

  const toggle = (type: StoryEntityType) => {
    const root = rootRef.current;
    if (!root || disabled) return;
    const span = selectionIn(root);
    if (!span || span.end <= span.start) return;
    if (type === 'text_url') {
      const current = readCaption(root);
      setLinkDraft({ ...span, url: linkAt(current.entities, span.start, span.end) ?? '' });
      return;
    }
    const current = readCaption(root);
    const entities = toggleEntity(
      current.entities,
      type,
      span.start,
      span.end,
      current.text.length,
    );
    if (entities.length > MAX_ENTITIES) return;
    commit({ text: current.text, entities }, span);
  };

  const closeLink = () => {
    const draft = linkDraft;
    setLinkDraft(null);
    const root = rootRef.current;
    if (root && draft) {
      root.focus();
      selectIn(root, draft);
    }
  };

  const applyLink = (url: string | null) => {
    const root = rootRef.current;
    if (!root || !linkDraft) return;
    const current = readCaption(root);
    const entities = setLink(
      current.entities,
      linkDraft.start,
      linkDraft.end,
      url,
      current.text.length,
    );
    const span = { start: linkDraft.start, end: linkDraft.end };
    setLinkDraft(null);
    root.focus();
    if (entities.length > MAX_ENTITIES) return;
    commit({ text: current.text, entities }, span);
  };

  const length = value.text.length;
  const overFree = premium === false && length > CAPTION_MAX_FREE;

  return (
    <div className="flex flex-col gap-2">
      <CaptionToolbar
        active={active}
        hasSelection={hasSelection}
        disabled={disabled}
        onToggle={toggle}
      />
      {linkDraft ? (
        <LinkPopover
          initialUrl={linkDraft.url}
          onApply={applyLink}
          onRemove={() => {
            applyLink(null);
          }}
          onCancel={closeLink}
        />
      ) : null}
      <div className="relative">
        {length === 0 ? (
          <span aria-hidden="true" className="pointer-events-none absolute left-3 top-2 type-small">
            {placeholder ?? t('storyComposer.caption.placeholder')}
          </span>
        ) : null}
        <div
          ref={rootRef}
          role="textbox"
          aria-multiline="true"
          aria-label={label ?? t('storyComposer.caption.label')}
          aria-disabled={disabled || undefined}
          aria-placeholder={placeholder ?? t('storyComposer.caption.placeholder')}
          contentEditable={!disabled}
          suppressContentEditableWarning
          spellCheck
          className={cn(
            areaBase({ size: 'md' }),
            'max-h-feed min-h-touch overflow-y-auto whitespace-pre-wrap break-words',
            disabled && 'opacity-60',
          )}
          onInput={() => {
            const root = rootRef.current;
            if (!root) return;
            const next = readCaption(root);
            if (next.text.length > CAPTION_MAX_PREMIUM) {
              commit(next, { start: CAPTION_MAX_PREMIUM, end: CAPTION_MAX_PREMIUM });
              return;
            }
            lastEmitted.current = next;
            onChange(next);
            refreshToolbar();
          }}
          onKeyDown={(event) => {
            if (event.nativeEvent.isComposing) return;
            if (event.key === 'Enter') {
              event.preventDefault();
              insertText('\n');
              return;
            }
            if ((event.ctrlKey || event.metaKey) && !event.altKey) {
              const entry = MARK_KEYS[event.key.toLowerCase()];
              if (entry && entry.shift === event.shiftKey) {
                event.preventDefault();
                toggle(entry.type);
              }
            }
          }}
          onPaste={(event) => {
            event.preventDefault();
            if (disabled) return;
            const text = event.clipboardData.getData('text/plain').replace(/\r\n?/g, '\n');
            if (text !== '') insertText(text);
          }}
          onDrop={(event) => {
            event.preventDefault();
          }}
        />
      </div>
      <p
        className={cn('type-small text-right', overFree && 'text-warning-deep')}
        aria-live="polite"
      >
        {overFree ? `${t('storyComposer.caption.overFree', { free: CAPTION_MAX_FREE })} · ` : ''}
        {t('storyComposer.caption.counter', { count: length, max: CAPTION_MAX_PREMIUM })}
      </p>
    </div>
  );
}
