const links = [
  ['Home', '/index.html'], ['How it works', '/pages/how-it-works.html'],
  ['Onboarding', '/pages/onboarding.html'], ['Dashboard', '/pages/dashboard.html'],
  ['Career intelligence', '/pages/career-intelligence.html'], ['Skill assessment', '/pages/skill-assessment.html'],
  ['Roadmap', '/pages/roadmap.html'], ['Accountability', '/pages/accountability.html'],
];
function siteHeader() {
  const current = location.pathname.endsWith('/') ? '/index.html' : location.pathname;
  return `<header class="site-header"><nav class="site-nav" aria-label="Main navigation"><a class="site-brand" href="/index.html">CareerCoach AI</a><div class="site-links">${links.map(([label, href]) => `<a href="${href}"${current === href ? ' aria-current="page"' : ''}>${label}</a>`).join('')}</div></nav></header>`;
}
export function mountSiteShell() {
  document.querySelector('[data-site-header]')?.insertAdjacentHTML('afterbegin', siteHeader());
  document.querySelector('[data-site-footer]')?.insertAdjacentHTML('afterbegin', '<footer class="site-footer"><div class="page-container"><p>CareerCoach AI · Your Personal AI Career Strategist.</p></div></footer>');
}
