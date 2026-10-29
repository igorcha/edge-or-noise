import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// In development the API runs on Django's dev server; in production Nginx serves both from one origin.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { '/api': process.env.API_URL ?? 'http://127.0.0.1:8000' },
  },
})
