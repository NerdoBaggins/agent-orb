// Injected into every panel by the orb server. Gives the panel `orb.send`, `orb.speak`, `orb.stop`, `orb.action`, `orb.state`,
// `orb.data` (what the user's feed for this panel last wrote) and `orb.on("state" | "data", fn)`;
// the only way out of the sandbox is postMessage to the orb page. `createOrbApi` is exported for tests.
// The sandbox gives the panel no origin, so the browser's storage throws on first touch; a page written for a normal
// tab would die there. Storage that throws is replaced with one that lives as long as the panel.
if (typeof window !== "undefined") {
  for (const name of ["localStorage", "sessionStorage"]) {
    try {
      void window[name];
    } catch {
      const items = new Map();
      Object.defineProperty(window, name, { configurable: true, value: {
        getItem: (k) => (items.has(String(k)) ? items.get(String(k)) : null),
        setItem: (k, v) => { items.set(String(k), String(v)); },
        removeItem: (k) => { items.delete(String(k)); },
        clear: () => items.clear(),
        key: (i) => [...items.keys()][i] ?? null,
        get length() { return items.size; },
      } });
    }
  }
}
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.orb = factory()({
    post: (m) => root.parent.postMessage(m, "*"),
    isActive: () => !!(root.navigator && root.navigator.userActivation && root.navigator.userActivation.isActive),
    listen: (fn) => root.addEventListener("message", (ev) => { if (ev.source === root.parent) fn(ev.data || {}); }),
    onKey: (fn) => root.addEventListener("keydown", fn),
  });
})(typeof window !== "undefined" ? window : globalThis, function () {
  return function createOrbApi({ post, isActive, listen, onKey }) {
    const pending = new Map();
    const listeners = { state: new Set(), data: new Set() };
    let state = "idle";
    let data = null, dataAt = null;
    const load = Math.random().toString(36).slice(2);
    let seq = 0;

    listen((m) => {
      if (m.orb === "state") {
        state = m.state;
        for (const fn of listeners.state) fn(state);
      } else if (m.orb === "data") {
        data = m.data;
        dataAt = m.at;
        for (const fn of listeners.data) fn(data, dataAt);
      } else if (m.orb === "reply" && pending.has(m.id)) {
        pending.get(m.id).resolve(m.text);
        pending.delete(m.id);
      } else if (m.orb === "result" && pending.has(m.id)) {
        pending.get(m.id).resolve(m.result);
        pending.delete(m.id);
      } else if (m.orb === "reject" && pending.has(m.id)) {
        pending.get(m.id).reject(new Error(m.error || "rejected"));
        pending.delete(m.id);
      }
    });

    // Focus inside a panel's frame keeps keys from the orb page, so Escape (back to the split view, or stop) is
    // handed over unless the panel used it itself or the user is typing.
    if (onKey) onKey((ev) => {
      const t = ev.target;
      const typing = t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable);
      if (ev.code !== "Escape" || ev.repeat || typing) return;
      // This listener is added before the panel's own, so whether the panel used the key is known only once the
      // event has been dispatched.
      setTimeout(() => { if (!ev.defaultPrevented) post({ orb: "key", code: "Escape" }); }, 0);
    });

    return {
      get state() { return state; },
      get data() { return data; },
      get dataAt() { return dataAt; },
      on(event, fn) {
        if (!listeners[event]) return () => {};
        listeners[event].add(fn);
        // Data that arrived before the page subscribed is handed over at once.
        if (event === "data" && data !== null) fn(data, dataAt);
        return () => listeners[event].delete(fn);
      },
      send(text, options) {
        const speak = !options || options.speak !== false;
        if (!isActive()) return Promise.reject(new Error("orb.send works only during a click or key press"));
        const id = `${load}:${++seq}`;
        return new Promise((resolve, reject) => {
          pending.set(id, { resolve, reject });
          post({ orb: "send", id, text: String(text), speak });
        });
      },
      // Reads `text` aloud in the orb's voice, with no agent turn; the orb's state follows it, and orb.stop() ends it.
      speak(text) {
        if (!isActive()) return Promise.reject(new Error("orb.speak works only during a click or key press"));
        if (state !== "idle") return Promise.reject(new Error("busy"));
        post({ orb: "speak", text: String(text) });
        return Promise.resolve();
      },
      stop() {
        post({ orb: "stop" });
      },
      // Runs one of the user's own commands for this panel (actions.json in their actions folder) with `args` as
      // JSON and resolves with its JSON answer. Whether it needs a click is the action's, checked by the orb.
      action(name, args) {
        const id = `${load}:${++seq}`;
        return new Promise((resolve, reject) => {
          pending.set(id, { resolve, reject });
          post({ orb: "action", id, name: String(name), args: args === undefined ? null : args });
        });
      },
    };
  };
});
