const navigation = document.querySelector('#primary-navigation');
const menuToggle = document.querySelector('.menu-toggle');
const featuresDropdown = document.querySelector('.navigation-dropdown');
const mobileNavigation = window.matchMedia('(max-width: 72rem)');

function closeMobileNavigation() {
  if (!navigation || !menuToggle) return;
  navigation.hidden = true;
  menuToggle.setAttribute('aria-expanded', 'false');
  menuToggle.setAttribute('aria-label', 'Open navigation');
}

function syncNavigationMode(event) {
  if (!navigation || !menuToggle) return;
  if (event.matches) {
    closeMobileNavigation();
  } else {
    navigation.hidden = false;
    menuToggle.setAttribute('aria-expanded', 'false');
    menuToggle.setAttribute('aria-label', 'Open navigation');
  }
}

if (navigation && menuToggle) {
  syncNavigationMode(mobileNavigation);
  mobileNavigation.addEventListener('change', syncNavigationMode);

  menuToggle.addEventListener('click', () => {
    const isExpanded = menuToggle.getAttribute('aria-expanded') === 'true';
    menuToggle.setAttribute('aria-expanded', String(!isExpanded));
    menuToggle.setAttribute('aria-label', isExpanded ? 'Open navigation' : 'Close navigation');
    navigation.hidden = isExpanded;
  });

  navigation.addEventListener('click', (event) => {
    if (event.target.closest('.dropdown-menu a')) {
      featuresDropdown?.removeAttribute('open');
    }
    if (event.target.closest('a') && mobileNavigation.matches) closeMobileNavigation();
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      closeMobileNavigation();
      navigation.querySelectorAll('details[open]').forEach((dropdown) => dropdown.removeAttribute('open'));
    }
  });
}

if (featuresDropdown) {
  document.addEventListener('pointerdown', (event) => {
    if (!featuresDropdown.contains(event.target)) {
      featuresDropdown.removeAttribute('open');
    }
  });
}
