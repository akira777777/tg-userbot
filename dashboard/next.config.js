const path = require("path");
const { loadEnvConfig } = require("@next/env");

loadEnvConfig(path.join(__dirname, ".."));

module.exports = {
  reactStrictMode: true,
  turbopack: { root: __dirname },
};
