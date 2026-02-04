import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { createProxyMiddleware } from 'http-proxy-middleware';
import type { Plugin } from 'vite';
import { config } from 'dotenv'
import tailwindcss from '@tailwindcss/vite'

config({ path: '.env' })
config({ path: '.env.local', override: true })

function influxProxy(): Plugin {
  return {
    name: 'influx-proxy',
    configureServer(server) {
      server.middlewares.use(
        '/influx/api/v2/',
        createProxyMiddleware({
          target: `http://${process.env.INFLUX_HOST}`,
          changeOrigin: true,
          pathRewrite: { '^/query': '/api/v2/query' },
          pathFilter: [ '/query', '/health' ],
          // logger: { error: console.error, info: console.info },
          on: {
            proxyReq: (proxyReq) => {
              if (process.env.INFLUX_TOKEN) {
                proxyReq.setHeader('Authorization', `Token ${process.env.INFLUX_TOKEN}`);
              }
            }
          }
        })
      );
      server.middlewares.use(
        '/sonos/',
        createProxyMiddleware({
          target: `http://${process.env.SONOS_HOST}`,
          changeOrigin: true,
          pathRewrite: { '^/sonos': '/' },
          logger: { error: console.error, info: console.info },
        })
      );
      server.middlewares.use(
        '/roborock/',
        createProxyMiddleware({
          target: `http://localhost:8080/`,
          changeOrigin: true,
          prependPath: true,
          logger: { error: console.error, info: console.info },
        })
      );
    },
    
  };
}

export default defineConfig({
  plugins: [tailwindcss(), react(), influxProxy()],
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