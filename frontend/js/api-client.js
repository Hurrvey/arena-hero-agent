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
  metrics() { return this.request("/metrics/summary"); }
  strategy() { return this.request("/strategy"); }
  adaptive() { return this.request("/adaptive/status"); }
  control(action) { return this.request(`/agent/${action}`, { method: "POST" }); }
  saveStrategy(payload) {
    return this.request("/strategy", { method: "PUT", body: JSON.stringify(payload) });
  }
}
