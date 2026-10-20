import { readFileSync, existsSync } from "node:fs";
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

// In production CDK writes /config.json next to the app. Locally, serve
// config.local.json (copied from the stack outputs) at the same path.
const localConfig = (): Plugin => ({
  name: "local-config",
  configureServer(server) {
    server.middlewares.use("/config.json", (_req, res) => {
      const file = new URL("./config.local.json", import.meta.url);
      if (!existsSync(file)) {
        res.statusCode = 404;
        res.end("Create frontend/config.local.json from config.example.json");
        return;
      }
      res.setHeader("content-type", "application/json");
      res.end(readFileSync(file));
    });
  },
});

export default defineConfig({
  plugins: [react(), localConfig()],
  server: { port: 5173, strictPort: true },
});
