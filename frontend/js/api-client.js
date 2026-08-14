export class ApiError extends Error {
  constructor(status, payload) {
    super(payload?.message || `Request failed (${status})`);
    this.name = "ApiError";
    this.status = status;
    this.code = payload?.code || "REQUEST_FAILED";
    this.details = payload?.details || {};
  }
}

export class ApiClient {
  constructor(base = "/api/v1") {
    this.base = base;
  }

  async request(path, options = {}) {
    const response = await fetch(`${this.base}${path}`, {
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    const payload = response.status === 204 ? null : await response.json();
    if (!response.ok) throw new ApiError(response.status, payload);
    return payload;
  }

  status() { return this.request("/agent/status"); }
  state() { return this.request("/state/current"); }
  plan() { return this.request("/plan/current"); }
  events(afterSeq = 0) { return this.request(`/events?afterSeq=${afterSeq}&limit=300`); }
  eventsTail() { return this.request("/events?tail=true&limit=300"); }
  metrics() { return this.request("/metrics/summary"); }
  metricSeries() { return this.request("/metrics/series"); }
  strategy() { return this.request("/strategy"); }
  strategySchema() { return this.request("/strategy/schema"); }
  strategyHistory() { return this.request("/strategy/history"); }
  adaptive() { return this.request("/adaptive/status"); }
  adaptiveReports() { return this.request("/adaptive/reports"); }
  settings() { return this.request("/settings"); }
  historyTick(tick) { return this.request(`/history/${encodeURIComponent(tick)}`); }
  async exploration(bounds, etag = null) {
    const query = new URLSearchParams({
      minX: String(bounds.minX),
      minY: String(bounds.minY),
      maxX: String(bounds.maxX),
      maxY: String(bounds.maxY),
    });
    const headers = etag ? { "If-None-Match": etag } : {};
    const response = await fetch(`${this.base}/exploration?${query}`, {
      credentials: "same-origin",
      headers,
    });
    if (response.status === 304) return { notModified: true, etag };
    const payload = await response.json();
    if (!response.ok) throw new ApiError(response.status, payload);
    return { payload, etag: response.headers.get("etag") };
  }
  decideCandidate(candidateId, payload) { return this.request(`/adaptive/candidates/${encodeURIComponent(candidateId)}`, { method:"POST", body:JSON.stringify(payload) }); }
  control(action) { return this.request(`/agent/${action}`, { method: "POST" }); }
  saveStrategy(payload) {
    return this.request("/strategy", { method: "PUT", body: JSON.stringify(payload) });
  }
  rollbackStrategy(payload) { return this.request("/strategy/rollback", { method:"POST", body:JSON.stringify(payload) }); }
}
