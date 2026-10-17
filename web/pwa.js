"use strict";
(() => {
  let installPrompt;
  const button = document.getElementById("install-app");
  const guide = document.getElementById("install-guide");
  const installed = () => matchMedia("(display-mode: standalone)").matches || navigator.standalone;
  button.hidden = installed();
  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    installPrompt = event;
  });
  window.addEventListener("appinstalled", () => { button.hidden = true; });
  button.addEventListener("click", async () => {
    if (installPrompt) {
      await installPrompt.prompt();
      await installPrompt.userChoice;
      installPrompt = null;
    } else guide.showModal();
  });
  document.getElementById("close-install-guide").addEventListener("click", () => guide.close());
  if ("serviceWorker" in navigator && window.isSecureContext) {
    navigator.serviceWorker.register("/sw.js", {updateViaCache: "none"}).catch(() => {});
  }
  const banner = document.getElementById("connection-notice");
  function connection() { banner.hidden = navigator.onLine; }
  window.addEventListener("online", connection);
  window.addEventListener("offline", connection);
  connection();
})();
