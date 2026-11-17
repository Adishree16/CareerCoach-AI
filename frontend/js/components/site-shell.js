const links = [
  ['Home', '/index.html'],
  ['Career Intelligence', '/pages/career-intelligence.html'],
  ['Roadmap', '/pages/roadmap.html'],
];
function siteHeader() {
  const current = location.pathname.endsWith('/') ? '/index.html' : location.pathname;
  const isRoadmap = document.body.classList.contains('roadmap-page');
  const brandClass = isRoadmap ? 'site-brand site-brand-roadmap' : 'site-brand';
  const trademark = isRoadmap ? '<sup>®</sup>' : '';
  return `<header class="site-header"><nav class="site-nav" aria-label="Main navigation"><a class="${brandClass}" href="/index.html" aria-label="CareerCoach AI home"><svg class="site-brand-mark" viewBox="0 0 40 36" aria-hidden="true" xmlns="http://www.w3.org/2000/svg"><defs><linearGradient id="site-brand-bars-gradient" x1="0" y1="1" x2="1" y2="0"><stop offset="0" stop-color="#7611eb"/><stop offset="1" stop-color="#8d24f5"/></linearGradient></defs><rect x="8" y="25" width="7" height="9" rx="1.8" fill="url(#site-brand-bars-gradient)"/><rect x="18" y="19" width="7" height="15" rx="1.8" fill="url(#site-brand-bars-gradient)"/><rect x="28" y="8" width="7" height="26" rx="1.8" fill="url(#site-brand-bars-gradient)"/></svg><span>CareerCoach AI${trademark}</span></a><div class="site-links">${links.map(([label, href]) => `<a href="${href}"${current === href ? ' aria-current="page"' : ''}>${label}</a>`).join('')}</div></nav></header>`;
}
export function mountSiteShell() {
  document.querySelector('[data-site-header]')?.insertAdjacentHTML('afterbegin', siteHeader());
  document.querySelector('[data-site-footer]')?.insertAdjacentHTML('afterbegin', '<footer class="site-footer"><div class="page-container"><p>CareerCoach AI · Your Personal AI Career Strategist.</p></div></footer>');
}
