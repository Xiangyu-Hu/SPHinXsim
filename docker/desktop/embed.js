/* Host-neutral embedding for the pinned KasmVNC 1.5.0 client. No credentials cross this bridge. */
(() => {
  const query = new URLSearchParams(location.search);
  if (query.get("sphinxsim_embed") !== "1") return;
  const origin = query.get("sphinxsim_origin");
  // Extra schemes are selected by the image owner, never by the query string.
  const customOrigins = globalThis.SPHINXSIM_EMBED_CUSTOM_ORIGINS || [];
  let validOrigin = false;
  try { const parsed = new URL(origin); validOrigin = /^https?:$/.test(parsed.protocol) && parsed.origin === origin; } catch {}
  if (!origin || !(validOrigin || customOrigins.includes(origin)) || window.parent === window) return;
  const identity = { protocol: "sphinxsim-desktop", version: 1, connectionId: query.get("sphinxsim_connection"), generation: query.get("sphinxsim_generation") };
  if (!identity.connectionId || !identity.generation) return;
  document.documentElement.classList.add("sphinxsim-embedded");
  parent.postMessage({ ...identity, type: "hello" }, origin);
  const delays = [1000, 2000, 4000, 8000, 15000];
  let ui, current, painted = false, attempt = 0, timer, terminal = false, retained, cleanup;
  let blocked = true;
  const send = (state, reason) => parent.postMessage({ ...identity, state, reason, attempt }, origin);
  const clearRetained = () => { cleanup?.(); retained?.remove(); cleanup = retained = undefined; };
  const mask = () => { if (current) current.viewOnly = blocked || !painted; };
  window.__sphinxsimEmbedded = {
    attach(client, controller) {
      ui = controller; current = client; painted = false; mask();
      // New framebuffer stays hidden until a real decoded rectangle has been painted.
      client._screen.style.visibility = "hidden";
      const dispose = client._display.dispose.bind(client._display);
      client._display.dispose = () => {
        if (client === current && painted) {
          cleanup?.(); cleanup = dispose;
          if (client._display._frameStatsInterval) clearInterval(client._display._frameStatsInterval);
        } else dispose();
      };
      client.addEventListener("connect", () => { if (client === current) send("connected"); });
      client.addEventListener("securityfailure", () => { terminal = true; clearTimeout(timer); send("error", "auth"); });
      client.addEventListener("disconnect", (event) => {
        if (client !== current || terminal) return;
        blocked = true; mask();
        if ([401, 403].includes(event.detail?.code)) { terminal = true; send("error", "auth"); return; }
        if (event.detail?.clean) { terminal = true; send("error", "ended"); return; }
        if (attempt >= delays.length) { terminal = true; send("error", "exhausted"); return; }
        // A rejected WebSocket handshake exposes only code 1006 to browsers.
        // Check the same protected resource to distinguish revoked access from a network drop.
        void fetch(location.href, { cache: "no-store", credentials: "same-origin", signal: AbortSignal.timeout(5000) }).then((response) => {
          void response.body?.cancel();
          if ([401, 403, 410].includes(response.status)) { terminal = true; clearTimeout(timer); send("error", response.status === 410 ? "ended" : "auth"); }
        }).catch(() => {});
        const delay = delays[attempt++]; send("disconnected");
        timer = setTimeout(() => { if (!terminal) { ui.inhibitReconnect = true; ui.connect(null, ui.reconnectPassword); } }, delay);
      });
    },
    retain(client) {
      if (client !== current || !painted) return false;
      retained?.remove(); retained = client._screen;
      retained.style.pointerEvents = "none"; retained.style.position = "absolute"; retained.style.inset = "0";
      return true;
    },
    painted(client) {
      if (client !== current || painted || terminal) return;
      painted = true; client._screen.style.visibility = "visible";
      clearRetained(); mask(); send("first-frame");
    },
  };
  window.addEventListener("message", (event) => {
    const data = event.data;
    if (event.source !== parent || event.origin !== origin || !data || Object.keys(identity).some((key) => data[key] !== identity[key]) || data.action !== "configure") return;
    blocked = data.blocked !== false; mask();
    if (ui && ["scale", "off"].includes(data.fit)) { ui.forceSetting("resize", data.fit, false); ui.applyResizeMode(); }
  });
  window.addEventListener("pagehide", () => { terminal = true; clearTimeout(timer); clearRetained(); });
})();
