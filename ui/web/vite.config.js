import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

// 后端端口默认为 8000；若 FastAPI 实际运行在 8001，请通过
// 环境变量 VITE_PROXY_TARGET=http://localhost:8001 覆盖，或直接修改此处。
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  // 使用 127.0.0.1 而非 localhost，避免 Node 优先解析到 IPv6 ::1 导致 ECONNREFUSED
  const target = env.VITE_PROXY_TARGET || 'http://127.0.0.1:8000'
  return {
    plugins: [vue()],
    server: {
      port: 5173,
      proxy: {
        '/api': { target, changeOrigin: true },
      },
    },
  }
})