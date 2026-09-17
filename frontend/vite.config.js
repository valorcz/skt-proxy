import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

// https://vite.dev/config/
export default defineConfig({
  plugins: [svelte()],
  base: '/static/dist/',
  build: {
    outDir: process.env.VITE_OUT_DIR || '../src/skt_proxy/static/dist',
    emptyOutDir: true,
  },
  server: {
    proxy: {
      '/api': 'http://localhost:5000',
      '/proxy_download': 'http://localhost:5000',
      '/static/covers': 'http://localhost:5000',
    }
  }
});
