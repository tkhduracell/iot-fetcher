import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { createProxyMiddleware } from 'http-proxy-middleware';
import type { Plugin } from 'vite';
import { config } from 'dotenv'
import tailwindcss from '@tailwindcss/vite'

config({ path: '.env' })

function flaskProxy(): Plugin {
  return {
    name: 'flask-proxy',
    configureServer(server) {
      server.middlewares.use(
        createProxyMiddleware({
          target: 'http://localhost:8080',
          changeOrigin: true,
          pathFilter: (pathname) => {
            // Exclude static assets and root path - let Vite handle these
            return pathname.startsWith('/query') || 
              pathname.startsWith('/health') ||
              pathname.startsWith('/influx') ||
              pathname.startsWith('/home/tasks');
          },
          logger: {
            error: console.error,
            info: console.info
          }
        })
      );
    },
  };
}

export default defineConfig({
  plugins: [tailwindcss(), react(), flaskProxy()],
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
  server: {
    port: process.env.WEB_UI_PORT ? Number(process.env.WEB_UI_PORT) : 3000,
    host: '127.0.0.1'
  },
  publicDir: '',
});