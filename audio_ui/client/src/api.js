// Thin fetch wrapper: JSON in/out, `{ error }` bodies become exceptions.
async function request(method, path, { params, body, form } = {}) {
  const url = new URL(`/audio${path}`, window.location.origin);
  for (const [key, value] of Object.entries(params || {})) {
    if (value !== undefined && value !== null && value !== "") url.searchParams.set(key, value);
  }
  const response = await fetch(url, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: form !== undefined ? form : body !== undefined ? JSON.stringify(body) : undefined,
  });
  const text = await response.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { error: text };
  }
  if (!response.ok) throw new Error((data && data.error) || `Error ${response.status}`);
  return data;
}

export const api = {
  status: () => request("GET", "/api/status"),
  devices: () => request("GET", "/api/devices"),
  settings: () => request("GET", "/api/settings"),
  updateSettings: (patch) => request("PUT", "/api/settings", { body: patch }),
  downloadModel: () => request("POST", "/api/models/download"),
  start: (body) => request("POST", "/api/sessions/start", { body }),
  stop: (id) => request("POST", `/api/sessions/${id}/stop`),
  sessions: (params) => request("GET", "/api/sessions", { params }),
  session: (id) => request("GET", `/api/sessions/${id}`),
  patchSession: (id, patch) => request("PATCH", `/api/sessions/${id}`, { body: patch }),
  deleteSession: (id) => request("DELETE", `/api/sessions/${id}`),
  retranscribe: (id) => request("POST", `/api/sessions/${id}/retranscribe`),
  peaks: (id, n = 400) => request("GET", `/api/sessions/${id}/peaks`, { params: { n } }),
  search: (params) => request("GET", "/api/search", { params }),
  tags: () => request("GET", "/api/tags"),
  importFile: (file, fields) => {
    const form = new FormData();
    form.append("file", file, file.name);
    for (const [key, value] of Object.entries(fields || {})) form.append(key, value);
    return request("POST", "/api/import", { form });
  },
  audioUrl: (id) => `/audio/api/sessions/${id}/audio`,
  exportUrl: (id, format) => `/audio/api/sessions/${id}/export?format=${format}`,
  liveUrl: (id) => `/audio/api/sessions/${id}/live`,
};
