import 'dotenv/config';

import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

const apiPort = Number(process.env.PORT ?? 6161);
const webPort = Number(process.env.WEB_PORT ?? 6160);

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: webPort,
    proxy: {
      '/api': `http://127.0.0.1:${apiPort}`,
      '/trpc': `http://127.0.0.1:${apiPort}`,
    },
  },
});
