import { STICKER_LABEL_MAX } from './limits';

export type LabelProblem = 'empty' | 'too_long' | 'emoji';

// The server renders the plate with a font that has no colour glyphs, so a pictographic
// character would come out as a box.
const PICTOGRAPHIC = /\p{Extended_Pictographic}/u;

export function labelProblem(label: string): LabelProblem | null {
  const text = label.trim();
  if (text === '') return 'empty';
  if (PICTOGRAPHIC.test(text)) return 'emoji';
  if ([...text].length > STICKER_LABEL_MAX) return 'too_long';
  return null;
}
