import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 开发态：WS 直连后端随机端口（启动器把端口写进 ECAT_PORT 提示）。
// 生产态：同源静态托管，无需代理。
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
});