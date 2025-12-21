import { defineConfig } from 'vite'

export default defineConfig({
  root: '.',
  build: {
    outDir: 'dist',
    assetsDir: 'assets',
    rollupOptions: {
      input: 'index.html'
    }
  },
  server: {
    port: 2125,
    proxy: {
      '/api': {
        target: 'http://localhost:2124',
        changeOrigin: true
      }
    }
  }
})
