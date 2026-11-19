const CACHE = "schwartz-spot-v3";
const APP_SHELL = [
  "./",
  "./index.html",
  "./styles.css",
  "./app.js",
  "./config.js",
  "./fonts/spot-mark.ttf",
  "./manifest.json",
  "./favicon.ico",
  "./apple-touch-icon.png",
  "./icons/favicon-32.png",
  "./icons/icon-48.png",
  "./icons/icon-192.png",
  "./icons/icon-512.png",
  "./icons/icon-512-maskable.png",
  "./icons/apple-touch-icon.png",
  "./data/index.json",
];

self.addEventListener("install", function (event) {
  event.waitUntil(
    caches
      .open(CACHE)
      .then(function (cache) {
        return cache.addAll(APP_SHELL).then(function () {
          return fetch("./data/index.json")
            .then(function (res) {
              if (!res.ok) return [];
              return res.json();
            })
            .then(function (data) {
              const dates = Array.isArray(data) ? data : (data && data.dates) || [];
              const paths = dates
                .map(function (item) {
                  const date = typeof item === "string" ? item : item && item.date;
                  return date ? "./data/" + date + ".json" : null;
                })
                .filter(Boolean);
              return paths.length ? cache.addAll(paths) : undefined;
            })
            .catch(function () {
              /* Day files will be cached on first visit. */
            });
        });
      })
      .then(function () {
        return self.skipWaiting();
      })
  );
});

self.addEventListener("activate", function (event) {
  event.waitUntil(
    caches
      .keys()
      .then(function (keys) {
        return Promise.all(
          keys
            .filter(function (key) {
              return key !== CACHE;
            })
            .map(function (key) {
              return caches.delete(key);
            })
        );
      })
      .then(function () {
        return self.clients.claim();
      })
  );
});

function isDataRequest(url) {
  return url.pathname.indexOf("/data/") !== -1 && url.pathname.endsWith(".json");
}

self.addEventListener("fetch", function (event) {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  if (isDataRequest(url)) {
    event.respondWith(networkFirst(req));
    return;
  }

  event.respondWith(
    caches.match(req).then(function (cached) {
      if (cached) return cached;
      return fetch(req)
        .then(function (res) {
          if (res && res.ok) {
            const copy = res.clone();
            caches.open(CACHE).then(function (cache) {
              cache.put(req, copy);
            });
          }
          return res;
        })
        .catch(function () {
          if (req.mode === "navigate") return caches.match("./index.html");
          return cached;
        });
    })
  );
});

function networkFirst(req) {
  return fetch(req)
    .then(function (res) {
      if (res && res.ok) {
        const copy = res.clone();
        caches.open(CACHE).then(function (cache) {
          cache.put(req, copy);
        });
      }
      return res;
    })
    .catch(function () {
      return caches.match(req);
    });
}
