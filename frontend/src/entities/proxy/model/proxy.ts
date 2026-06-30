// The design shows proxy types upper-cased (SOCKS5 / HTTPS); the API carries the
// lowercase enum.
export function proxyTypeLabel(type: string): string {
  return type.toUpperCase();
}
