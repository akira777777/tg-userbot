import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "server-only": new URL("./src/test/server-only.js", import.meta.url).pathname,
    },
  },
  test: {
    environment: 'node',
    include: ['src/__tests__/**/*.{test,spec}.{js,ts,tsx}'],
  },
});
