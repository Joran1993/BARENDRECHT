self.addEventListener("push", event => {
  let data = {};
  try { data = event.data.json(); } catch {}
  const title = data.title || "CIRQO";
  const body  = data.body  || "Nieuw aanbod ontvangen.";
  const url   = data.url   || "/bedrijf";
  event.waitUntil(
    self.registration.showNotification(title, {
      body,
      icon: "/static/icon-192.png",
      badge: "/static/icon-192.png",
      data: { url },
    })
  );
});

self.addEventListener("notificationclick", event => {
  event.notification.close();
  const url = event.notification.data?.url || "/bedrijf";
  event.waitUntil(clients.openWindow(url));
});
