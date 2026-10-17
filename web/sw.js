"use strict";
// Only provide an offline explanation. Never cache private articles, API responses,
// images, credentials, or login pages on a shared phone.
self.addEventListener("install", (event) => event.waitUntil(self.skipWaiting()));
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET" || event.request.mode !== "navigate") return;
  event.respondWith(fetch(event.request).catch(() => new Response(
    '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>纸飞机 · 暂时离线</title><body><h1>暂时没有网络</h1><p>连接网络后重新打开工作台。服务器上的排期继续执行；此页面无法保存新内容。</p><a href="/">重新连接</a></body></html>',
    {status: 503, headers: {"Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store"}},
  )));
});
