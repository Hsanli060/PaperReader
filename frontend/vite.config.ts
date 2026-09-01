import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  resolve: {
    // @ 别名 → src 目录（和 tsconfig 的 paths 配对：
    // tsconfig 管"类型检查时认得 @"，vite 管"打包时把 @ 换成真实路径"）
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    // 开发期把 /api 开头的请求转发给后端 —— 前端代码里只写相对路径 "/api/..."，
    // 浏览器以为请求发给自己（同源），vite 在背后转手给 uvicorn。
    // 这样开发期就不需要后端配 CORS（生产环境用 nginx 反向代理同理）。
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
