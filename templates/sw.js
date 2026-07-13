{% load static %}/* Heureux service worker — offline app shell. */
var CACHE = "heureux-v6";
var SHELL = [
  "/",
  "/review/",
  "{% url 'study:task_detail' 'orale' 'tache-3' %}",
  "{% url 'offline' %}",
  "{% static 'study/css/app.css' %}?v=6",
  "{% static 'study/js/app.js' %}?v=6",
  "/manifest.webmanifest",
  "{% static 'study/icons/icon-192.png' %}",
  "{% static 'study/icons/icon-512.png' %}",
  "{% static 'study/icons/logo.svg' %}"
];

self.addEventListener("install", function (event) {
  event.waitUntil(
    caches.open(CACHE).then(function (cache) {
      return cache.addAll(SHELL).catch(function () {});
    }).then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener("activate", function (event) {
  event.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(keys.map(function (k) {
        if (k !== CACHE) { return caches.delete(k); }
      }));
    }).then(function () { return self.clients.claim(); })
  );
});

self.addEventListener("fetch", function (event) {
  var req = event.request;
  if (req.method !== "GET") { return; }
  var url = new URL(req.url);
  if (url.origin !== self.location.origin) { return; }

  // Never intercept the dynamic review API (keep study state fresh).
  if (url.pathname.indexOf("/review/") === 0 && url.pathname !== "/review/") {
    return;
  }

  // Cache-first for versioned static assets.
  if (url.pathname.indexOf("/static/") === 0) {
    event.respondWith(
      caches.match(req).then(function (hit) {
        return hit || fetch(req).then(function (res) {
          var copy = res.clone();
          caches.open(CACHE).then(function (c) { c.put(req, copy); });
          return res;
        });
      })
    );
    return;
  }

  // Network-first for page navigations; fall back to cache then offline page.
  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req).then(function (res) {
        var copy = res.clone();
        caches.open(CACHE).then(function (c) { c.put(req, copy); });
        return res;
      }).catch(function () {
        return caches.match(req).then(function (hit) {
          return hit || caches.match("{% url 'offline' %}");
        });
      })
    );
  }
});
