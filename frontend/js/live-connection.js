export function reconnectDelay(attempt) {
  return Math.min(10_000, 500 * 2 ** Math.max(0, attempt));
}

export class LiveConnection {
  constructor({ store, api, onEvent }) {
    this.store = store;
    this.api = api;
    this.onEvent = onEvent;
    this.socket = null;
    this.closed = false;
    this.attempt = 0;
    this.timer = null;
  }

  start() {
    this.closed = false;
    this.connect();
  }

  stop() {
    this.closed = true;
    clearTimeout(this.timer);
    this.socket?.close();
  }

  connect() {
    if (this.closed) return;
    this.store.setConnection({ status: this.attempt ? "RECONNECTING" : "CONNECTING", stale: true });
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    const after = this.store.snapshot.lastSeq;
    this.socket = new WebSocket(`${protocol}//${location.host}/ws/v1/live?afterSeq=${after}`);
    this.socket.addEventListener("open", () => {
      this.attempt = 0;
      this.store.setConnection({ status: "LIVE", stale: false });
    });
    this.socket.addEventListener("message", async ({ data }) => {
      const message = JSON.parse(data);
      if (message.type === "hello") return;
      if (message.type === "eventGap") {
        await this.recover();
        return;
      }
      if (Number.isSafeInteger(Number(message.seq)) && this.store.applyEvent(message)) {
        this.onEvent?.(message);
      }
      if (this.store.snapshot.connection.needsReplay) await this.recover();
    });
    this.socket.addEventListener("close", () => this.scheduleReconnect());
    this.socket.addEventListener("error", () => this.socket?.close());
  }

  async recover() {
    this.store.setConnection({ status: "RECONNECTING", stale: true });
    const [state, plan, events] = await Promise.all([
      this.api.state().catch(() => null),
      this.api.plan().catch(() => null),
      this.api.eventsTail(),
    ]);
    this.store.replaceFromRest(state, plan, events.lastSeq, events.events || []);
  }

  scheduleReconnect() {
    if (this.closed) return;
    this.store.setConnection({ status: "RECONNECTING", stale: true });
    const delay = reconnectDelay(this.attempt++);
    this.timer = setTimeout(() => this.connect(), delay);
  }
}
