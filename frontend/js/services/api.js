import { API_BASE_URL } from '../config.js';

async function request(path, { method = 'GET', body, headers = {}, signal } = {}) {
  let response;
  try {
    response = await fetch(`${API_BASE_URL}${path.startsWith('/') ? path : `/${path}`}`, {
      method, signal, headers: { ...(body === undefined ? {} : { 'Content-Type': 'application/json' }), ...headers },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
  } catch (error) {
    if (error.name === 'AbortError') throw error;
    throw new Error('The server could not be reached. Check your connection and try again.', { cause: error });
  }
  const contentType = response.headers.get('content-type') || '';
  const data = contentType.includes('application/json') ? await response.json() : await response.text();
  if (!response.ok) {
    const detail = typeof data === 'object' && data !== null ? data.detail : data;
    throw new Error(detail || `Request failed (${response.status}).`);
  }
  return data;
}
export const apiGet = (path, options) => request(path, options);
export const apiPost = (path, body, options = {}) => request(path, { ...options, method: 'POST', body });
