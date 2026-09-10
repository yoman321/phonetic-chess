import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // globals: false keeps describe/it/expect/vi out of the global scope, so
  // eslint's no-undef (via js.configs.recommended) stays satisfied without an
  // eslint change. Tests import them from "vitest" and call cleanup() by hand.
  test: {
    environment: 'jsdom',
    globals: false,
  },
})
