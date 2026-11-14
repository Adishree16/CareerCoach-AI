const loginForm = document.querySelector('#login-form');
const emailInput = document.querySelector('#login-email');
const passwordInput = document.querySelector('#login-password');
const passwordToggle = document.querySelector('.password-toggle');
const loginButton = document.querySelector('#login-submit');
const forgotLink = document.querySelector('#forgot-password');
const loginMessage = document.querySelector('#login-message');

function updateLoginButton() {
  loginButton.disabled = !emailInput.value.trim() || !passwordInput.value.trim();
}

emailInput.addEventListener('input', updateLoginButton);
emailInput.addEventListener('change', updateLoginButton);
passwordInput.addEventListener('input', updateLoginButton);
passwordInput.addEventListener('change', updateLoginButton);
updateLoginButton();

passwordToggle.addEventListener('click', () => {
  const isHidden = passwordInput.type === 'password';
  passwordInput.type = isHidden ? 'text' : 'password';
  passwordToggle.setAttribute('aria-pressed', String(isHidden));
  passwordToggle.setAttribute('aria-label', `${isHidden ? 'Hide' : 'Show'} password`);
});

forgotLink.addEventListener('click', (event) => {
  event.preventDefault();
  loginMessage.textContent = 'Password recovery will be available after backend integration.';
});

loginForm.addEventListener('submit', (event) => {
  event.preventDefault();
  updateLoginButton();
  if (loginButton.disabled) return;
  loginMessage.textContent = 'Login authentication will be available after backend integration.';
});
