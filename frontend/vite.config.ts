import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Builds straight into Flask's own static folder (highsociety/web/static/dist)
// so web_server.py can hand back the built index.html and its assets with
// zero new serving logic -- see web_server.py's index() route and
// FRONTEND_DIST_DIR. Fixed (non-hashed) output filenames, deliberately:
// this isn't a CDN-fronted deployment with a manifest-parsing step on the
// Flask side, so a stable filename is simpler than teaching the backend to
// read Vite's manifest.json for a hashed one. During local development,
// `npm run dev`'s own dev server (proxying /api and /ws to Flask -- see
// server.proxy below) is what you actually run against, not this build
// output; `npm run build` only matters for what gets deployed.
export default defineConfig({
  plugins: [react()],
  // Flask serves this build's own output from /static/dist/ (its default
  // static_url_path + outDir below) -- without this, the built index.html
  // references plain root-absolute paths like /assets/index.js, which
  // 404 once actually served from under /static/dist/.
  base: '/static/dist/',
  build: {
    outDir: '../highsociety/web/static/dist',
    emptyOutDir: true,
    rollupOptions: {
      output: {
        entryFileNames: 'assets/[name].js',
        chunkFileNames: 'assets/[name]-[hash].js', // hashed is fine/expected for code-split chunks; only the entry needs a stable name
        assetFileNames: 'assets/[name][extname]',
      },
    },
  },
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
      '/ws': { target: 'ws://localhost:8000', ws: true },
      '/ws_spectate': { target: 'ws://localhost:8000', ws: true },
    },
  },
})
