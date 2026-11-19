const path = require("path");
const { loadEnvConfig } = require("@next/env");

loadEnvConfig(path.join(__dirname, ".."));

module.exports = {
  reactStrictMode: true,
  turbopack: { root: __dirname },
  // В dev панель открывают не с localhost (LAN, эмулятор, Telegram WebView).
  allowedDevOrigins: ["192.168.0.186", "127.0.0.1", "localhost"],
};
