import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'
import fs from 'node:fs'
import path from 'node:path'

const readmePlugin = {
  name: 'virtual:readme',
  resolveId(id) {
    if (id === 'virtual:readme') return '\0virtual:readme'
  },
  load(id) {
    if (id === '\0virtual:readme') {
      const content = fs.readFileSync(
        path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../README.md'),
        'utf-8'
      )
      return `export default ${JSON.stringify(content)}`
    }
  }
}

export default defineConfig({
  plugins: [vue(), readmePlugin],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url))
    }
  },
  server: {
    port: 3000,
    fs: {
      allow: ['..']
    },
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        // Disable response buffering so SSE events reach the browser immediately
        configure: (proxy) => {
          proxy.on('proxyRes', (proxyRes) => {
            if (proxyRes.headers['content-type']?.includes('text/event-stream')) {
              proxyRes.headers['x-accel-buffering'] = 'no'
            }
          })
        },
      },
      '/ws': {
        target: 'ws://localhost:8000',
        ws: true,
      },
      '/uploads': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      }
    }
  }
})
