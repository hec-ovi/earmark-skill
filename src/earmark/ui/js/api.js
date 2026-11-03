// HTTP client for docs/HTTP_API.md. Every response is the envelope {ok, data, error, meta}.

export class ApiError extends Error {
  constructor(status, error) {
    super((error && error.message) || 'Request failed');
    this.status = status;
    this.code = (error && error.code) || 'internal_error';
    this.hint = (error && error.hint) || null;
    this.details = (error && error.details) || null;
    this.retriable = Boolean(error && error.retriable);
  }
}

export async function api(method, path, body) {
  const init = { method, headers: { Accept: 'application/json' }, credentials: 'same-origin' };
  if (method !== 'GET') {
    init.headers['X-Earmark'] = '1';
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body === undefined ? {} : body);
  }
  let res;
  try {
    res = await fetch('/api' + path, init);
  } catch (e) {
    throw new ApiError(0, {
      code: 'network',
      message: 'Cannot reach the earmark server',
      hint: 'Is `earmark ui` still running? Restart it and this page reconnects.',
    });
  }
  let env = null;
  try {
    env = await res.json();
  } catch (e) {
    throw new ApiError(res.status, { code: 'internal_error', message: `Unexpected response from the server (HTTP ${res.status})` });
  }
  if (!env || env.ok !== true) {
    throw new ApiError(res.status, (env && env.error) || { message: `HTTP ${res.status}` });
  }
  return env.data;
}

export const get = (path) => api('GET', path);

/** Build a query string; arrays repeat the key, empty values are skipped. */
export function qs(params) {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === '' || v === false) continue;
    if (Array.isArray(v)) v.forEach((x) => sp.append(k, x));
    else sp.append(k, v === true ? '1' : String(v));
  }
  const s = sp.toString();
  return s ? '?' + s : '';
}

export const enc = encodeURIComponent;

/**
 * Upload one file as raw bytes (POST /api/issues/{key}/attachments). XHR for upload progress.
 * Resolves to the Attachment; rejects with ApiError (status 413 when over the size limit).
 */
export function uploadFile(key, file, { comment, onProgress } = {}) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const q = qs({ name: file.name || 'pasted-file', comment: comment || undefined });
    xhr.open('POST', `/api/issues/${enc(key)}/attachments${q}`);
    xhr.setRequestHeader('X-Earmark', '1');
    xhr.setRequestHeader('Content-Type', file.type || 'application/octet-stream');
    xhr.responseType = 'json';
    if (onProgress) xhr.upload.addEventListener('progress', (e) => { if (e.lengthComputable) onProgress(e.loaded, e.total); });
    xhr.addEventListener('load', () => {
      const env = xhr.response;
      if (env && env.ok === true) resolve(env.data);
      else reject(new ApiError(xhr.status, (env && env.error) || { message: `Upload failed (HTTP ${xhr.status})` }));
    });
    xhr.addEventListener('error', () => reject(new ApiError(xhr.status || 0, {
      code: 'network', message: xhr.status === 413 ? 'File is too large' : 'Upload failed: the server closed the connection',
    })));
    xhr.send(file);
  });
}
