self.addEventListener("push", event => {
  const data = event.data ? event.data.json() : {};
  event.waitUntil(
    self.registration.showNotification(data.title || "News Alert", {
      body: data.body || "",
      icon: "/static/icon.png",
      badge: "/static/icon.png",
      data: { url: data.url || "/" },
      requireInteraction: false,
    })
  );
});

self.addEventListener("notificationclick", event => {
  event.notification.close();
  event.waitUntil(
    clients.matchAll({ type: "window" }).then(list => {
      for (const client of list) {
        if (client.url === event.notification.data.url && "focus" in client)
          return client.focus();
      }
      if (clients.openWindow) return clients.openWindow(event.notification.data.url);
    })
  );
});
