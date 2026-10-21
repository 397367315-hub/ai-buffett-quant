import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  base: '/roundtable/',
  plugins: [react()],
  build: {
    outDir: '../../../frontend/public/roundtable',
    emptyOutDir: true,
  },
});
