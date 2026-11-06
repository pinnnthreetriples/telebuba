// UTF-16 code-unit length: what Telegram counts and what `.length` returns, so an
// emoji outside the BMP counts 2. Named so a reader sees the choice was deliberate.
export function utf16Length(text: string): number {
  return text.length;
}

// Cut `text` to at most `max` UTF-16 units without splitting a surrogate pair.
export function truncateUtf16(text: string, max: number): string {
  if (text.length <= max) return text;
  let end = max;
  const last = text.charCodeAt(end - 1);
  if (end > 0 && last >= 0xd800 && last <= 0xdbff) end -= 1;
  return text.slice(0, end);
}
