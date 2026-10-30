import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// In development the API runs on Django's dev server; in production Django serves both from one origin
// (the API, and this build through WhiteNoise).
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { '/api': process.env.API_URL ?? 'http://127.0.0.1:8000' },
  },
})
