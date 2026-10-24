import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import { resolveApiUrl } from './config/api-url.js'

export default defineConfig(({ command, mode }) => {
  const env = loadEnv(mode, process.cwd(), 'VITE_')
  const apiUrl = resolveApiUrl(env.VITE_API_URL, command === 'build')
  return {
    plugins: [react()],
    define: { 'import.meta.env.VITE_API_URL': JSON.stringify(apiUrl) },
    server: { host: '127.0.0.1', port: 5173, strictPort: true },
  }
})
