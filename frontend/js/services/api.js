import { API_BASE_URL } from '../config.js';

const CSRF_HEADER = 'X-CSRF-Token';
const STATE_CHANGING_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);
let csrfTokenInMemory = null;
let csrfTokenRequest = null;

async function obtainCsrfToken() {
  if (csrfTokenInMemory) return csrfTokenInMemory;
  if (!csrfTokenRequest) {
    csrfTokenRequest = (async () => {
      const response = await fetch(`${API_BASE_URL}/api/auth/csrf`, {
        method: 'GET',
        credentials: 'include',
        headers: { Accept: 'application/json' },
      });
      if (!response.ok) {
        const error = new Error('Unable to prepare a secure request. Please try again.');
        error.status = response.status;
        throw error;
      }
      const data = await response.json();
      if (typeof data?.csrf_token !== 'string' || !data.csrf_token) {
        throw new Error('Unable to prepare a secure request. Please try again.');
      }
      csrfTokenInMemory = data.csrf_token;
      return csrfTokenInMemory;
    })().finally(() => {
      csrfTokenRequest = null;
    });
  }
  return csrfTokenRequest;
}

async function request(path, { method = 'GET', body, headers = {}, signal } = {}) {
  const requestMethod = method.toUpperCase();
  const requestHeaders = { ...headers };
  if (STATE_CHANGING_METHODS.has(requestMethod)) {
    requestHeaders[CSRF_HEADER] = await obtainCsrfToken();
  }

  let response;
  try {
    response = await fetch(`${API_BASE_URL}${path.startsWith('/') ? path : `/${path}`}`, {
      method: requestMethod, signal, credentials: 'include', headers: { ...(body === undefined ? {} : { 'Content-Type': 'application/json' }), ...requestHeaders },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
  } catch (error) {
    if (error.name === 'AbortError') throw error;
    throw new Error('The server could not be reached. Check your connection and try again.', { cause: error });
  }
  const contentType = response.headers.get('content-type') || '';
  const data = contentType.includes('application/json') ? await response.json() : await response.text();
  const authSessionPath = /\/api\/auth\/(login|register|refresh|logout)(?:\?|$)/.test(path);
  if (authSessionPath) csrfTokenInMemory = null;
  if (!response.ok) {
    const detail = typeof data === 'object' && data !== null ? data.detail : data;
    const error = new Error(detail || `Request failed (${response.status}).`);
    error.status = response.status;
    throw error;
  }
  return data;
}
export const apiGet = (path, options) => request(path, options);
export const apiPost = (path, body, options = {}) => request(path, { ...options, method: 'POST', body });
export const apiPut = (path, body, options = {}) => request(path, { ...options, method: 'PUT', body });
