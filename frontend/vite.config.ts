import { defineConfig, loadEnv } from "vite";
import vue from "@vitejs/plugin-vue";
import { fileURLToPath, URL } from "node:url";

export default defineConfig(({ mode }) => {
  const root = fileURLToPath(new URL("../", import.meta.url));
  const env = loadEnv(mode, root, "");
  return {
    plugins: [vue()],
    // 只在 Node 端读取代理配置，绝不把根目录密钥注入浏览器。
    server: {
      host: env.FRONTEND_HOST || "127.0.0.1",
      port: Number(env.FRONTEND_PORT || 5173),
      strictPort: true,
      proxy: {
        "/api": { target: env.FRONTEND_API_TARGET || "http://127.0.0.1:8000" },
      },
    },
  };
});
