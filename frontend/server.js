// Serves the dashboard (public/) and forwards /api/* to the FastAPI backend,
// so the browser talks to one origin and no CORS setup is needed.
//
//   npm --prefix frontend install
//   npm --prefix frontend start        # http://localhost:3000
//
// API_URL (default http://127.0.0.1:8000) and PORT (default 3000) are configurable.
const path = require("path");
const express = require("express");
const { createProxyMiddleware } = require("http-proxy-middleware");

const API_URL = process.env.API_URL || "http://127.0.0.1:8000";
const PORT = Number(process.env.PORT || 3000);

const app = express();
app.use("/api", createProxyMiddleware({ target: `${API_URL}/api`, changeOrigin: true }));
app.use(express.static(path.join(__dirname, "public")));

app.listen(PORT, process.env.HOST || '127.0.0.1', () => {
  console.log(`ConnectomeChronicle dashboard on http://localhost:${PORT} (API ${API_URL})`);
});
