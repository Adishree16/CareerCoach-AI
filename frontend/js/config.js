const configuredApiBaseUrl = document.querySelector('meta[name="api-base-url"]')?.content;
export const API_BASE_URL = (configuredApiBaseUrl || 'https://careercoach-ai-t9pe.onrender.com').replace(/\/$/, '');
