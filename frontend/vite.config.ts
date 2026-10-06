import { fileURLToPath, URL } from 'node:url';

import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  // `motion` подключён только библиотечными блоками каталога. На холодном кэше
  // (CI) Vite находил его уже после загрузки страницы, пересобирал зависимости и
  // перезагружал её посреди теста — «Execution context was destroyed». Собранный
  // заранее, он к первому запросу уже готов.
  optimizeDeps: { include: ['motion/react'] },
  server: {
    // Dev: proxy the JSON API to the single-worker uvicorn backend so the SPA
    // and API share an origin (no CORS) exactly like the prod static mount.
    //
    // `changeOrigin` НЕ ставится, и это не упущение. Он переписывает `Host` на адрес
    // бэкенда, а `OriginProtectionMiddleware` берёт из `Host` тот origin, с которым
    // сверяет присланный браузером `Origin` (api/_middleware.py). Переписанный `Host`
    // делает их разными, и КАЖДЫЙ небезопасный запрос из дев-интерфейса, как только
    // появилась cookie сессии, отбивается 403 `untrusted_origin` — включая сам вход,
    // который на странице читается как «Неверные учётные данные».
    proxy: {
      '/api': { target: 'http://127.0.0.1:8080' },
    },
  },
});
