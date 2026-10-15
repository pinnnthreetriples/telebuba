// One lead per line: the number, then optionally a comma (or semicolon, or tab) and
// the client's name. Spaces belong to the number, so they never split it.
export function leadsFrom(value: string): { phone: string; name?: string }[] {
  return value
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [phone = '', ...rest] = line.split(/[,;\t]/);
      const name = rest.join(' ').trim();
      return name ? { phone: phone.trim(), name } : { phone: phone.trim() };
    });
}
