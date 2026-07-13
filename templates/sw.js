{% load static %}/* Heureux service worker — offline app shell. */
var CACHE = "heureux-v25";
var SHELL = [
  "{% url 'offline' %}",
  "{% static 'study/css/app.css' %}?v=24",
  "{% static 'study/js/app.js' %}?v=22",
  "{% static 'study/js/translate.js' %}?v=4",
  "/manifest.webmanifest",
  "{% static 'study/icons/icon-192.png' %}?v=2",
  "{% static 'study/icons/icon-512.png' %}?v=2",
  "{% static 'study/icons/logo.svg' %}?v=2"
];

self.addEventListener("install", function (event) {
  event.waitUntil(
    caches.open(CACHE).then(function (cache) {
      return cache.addAll(SHELL);
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

  // Never persist account-specific pages in a cache shared by browser users.
  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req, { cache: "no-store" }).catch(function () {
        return caches.match("{% url 'offline' %}");
      })
    );
  }
});
