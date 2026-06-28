import '@testing-library/jest-dom/vitest';
import { afterEach, vi } from 'vitest';

import { client } from '@/shared/api/client.gen';

// Node's fetch (undici) rejects relative URLs that a browser would resolve, and
// the generated client captures globalThis.fetch at import. So for tests give
// the client an absolute base and a controllable fetch; tests drive responses
// via vi.mocked(fetch).
vi.stubGlobal('fetch', vi.fn());
client.setConfig({ baseUrl: 'http://localhost', fetch: globalThis.fetch });

afterEach(() => {
  vi.mocked(fetch).mockReset();
});
