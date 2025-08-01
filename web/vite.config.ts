import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from "path"

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "./src"),
    },
  },
  build: {
    rollupOptions: {
      onwarn(warning, warn) {
        // Suppress DuckDB/Arrow sourcemap warnings
        if (warning.message.includes('Sourcemap for') && warning.message.includes('outside its package')) return;
        warn(warning);
      }
    }
  }
})
