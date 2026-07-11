import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      // ws:true so the container terminal's websocket proxies through too
      '/api': { target: 'http://localhost:8000', ws: true },
    },
  },
})
