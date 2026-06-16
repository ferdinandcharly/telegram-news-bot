// Prise de contrôle immédiate (la page est "contrôlée" → critère d'installabilité)
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", event => event.waitUntil(self.clients.claim()));

// Handler fetch minimal : sans lui, Android Chrome propose un simple raccourci
// (icône générique + barre Chrome) au lieu de "Installer l'application".
self.addEventListener("fetch", () => {});

self.addEventListener("push", event => {
  const data = event.data ? event.data.json() : {};
  event.waitUntil(
    self.registration.showNotification(data.title || "News Alert", {
      body: data.body || "",
      icon: "/static/icon-192.png",
      badge: "/static/icon-192.png",
      data: { url: data.url || "/" },
      requireInteraction: false,
    })
  );
});

self.addEventListener("notificationclick", event => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "/";
  event.waitUntil(
    clients.matchAll({ type: "window", includeUncontrolled: true }).then(list => {
      // si une fenêtre est déjà ouverte : la rediriger vers l'URL de la notif puis la focus
      for (const client of list) {
        if ("focus" in client) {
          if ("navigate" in client) { client.navigate(url).catch(() => {}); }
          return client.focus();
        }
      }
      if (clients.openWindow) return clients.openWindow(url);
    })
  );
});
