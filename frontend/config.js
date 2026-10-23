// Use the local API when served from localhost; deployed hosts use Render.
const localDevelopmentHosts = ["localhost", "127.0.0.1", "[::1]"];
const isLocalDevelopment = localDevelopmentHosts.includes(window.location.hostname);
window.LABORLINK_API_URL = isLocalDevelopment
  ? "http://127.0.0.1:8000/api"
  : "https://laborlink-api1.onrender.com/api";
