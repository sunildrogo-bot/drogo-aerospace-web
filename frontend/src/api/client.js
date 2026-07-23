// Thin wrapper around fetch() for talking to the Flask API. Always sends
// credentials (cookies) so the existing session-based auth keeps working
// unchanged — this is what lets the backend stay exactly as it is instead
// of being rewritten for token auth.
async function request(path, options = {}) {
  const res = await fetch(path, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  });
  const isJson = (res.headers.get('content-type') || '').includes('application/json');
  const data = isJson ? await res.json() : null;
  if (!res.ok) {
    const message = (data && data.error) || `Request failed (${res.status})`;
    throw new Error(message);
  }
  return data;
}

export function apiGet(path) {
  return request(path, { method: 'GET' });
}

export function apiPost(path, body) {
  return request(path, { method: 'POST', body: JSON.stringify(body || {}) });
}

export function apiPut(path, body) {
  return request(path, { method: 'PUT', body: JSON.stringify(body || {}) });
}

export function apiDelete(path) {
  return request(path, { method: 'DELETE' });
}

// Multipart uploads (photos, KML, logos, etc.) — no Content-Type header,
// the browser sets the correct multipart boundary itself.
export async function apiUpload(path, formData, method = 'POST') {
  const res = await fetch(path, { method, credentials: 'include', body: formData });
  const isJson = (res.headers.get('content-type') || '').includes('application/json');
  const data = isJson ? await res.json() : null;
  if (!res.ok) {
    const message = (data && data.error) || `Upload failed (${res.status})`;
    throw new Error(message);
  }
  return data;
}
