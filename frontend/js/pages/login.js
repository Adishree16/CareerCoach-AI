import { apiPost } from '../services/api.js';

const loginForm = document.querySelector('#login-form');
const emailInput = document.querySelector('#login-email');
const passwordInput = document.querySelector('#login-password');
const passwordToggle = document.querySelector('.password-toggle');
const loginButton = document.querySelector('#login-submit');
const forgotLink = document.querySelector('#forgot-password');
const loginMessage = document.querySelector('#login-message');
let loginInProgress = false;

function updateLoginButton() {
  loginButton.disabled = loginInProgress || !emailInput.value.trim() || !passwordInput.value.trim();
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
  loginMessage.textContent = 'Password recovery is currently handled by the CareerCoach AI administrator. Please contact the administrator for assistance.';
});

loginForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (loginInProgress) return;

  const email = emailInput.value.trim();
  if (!email) {
    loginMessage.textContent = 'Enter your email address.';
    emailInput.focus();
    return;
  }
  emailInput.value = email;
  if (!emailInput.validity.valid) {
    loginMessage.textContent = 'Enter a valid email address.';
    emailInput.focus();
    return;
  }
  if (!passwordInput.value) {
    loginMessage.textContent = 'Enter your password.';
    passwordInput.focus();
    return;
  }

  updateLoginButton();
  if (loginButton.disabled) return;

  loginInProgress = true;
  updateLoginButton();
  loginButton.setAttribute('aria-busy', 'true');
  loginMessage.textContent = 'Signing in...';
  try {
    const result = await apiPost('/api/auth/login', {
      email,
      password: passwordInput.value,
    });
    if (result?.authenticated !== true) {
      throw new Error('Login was not completed.');
    }
    loginMessage.textContent = 'Login successful. Redirecting...';
    window.location.href = 'profile.html';
  } catch (error) {
    if (error?.message?.includes('Request failed (401)')) {
      loginMessage.textContent = 'Invalid email or password.';
    } else if (error?.message?.includes('server could not be reached')) {
      loginMessage.textContent = 'The login service could not be reached. Please try again later.';
    } else {
      loginMessage.textContent = 'Unable to sign in right now. Please try again.';
    }
  } finally {
    loginInProgress = false;
    loginButton.setAttribute('aria-busy', 'false');
    updateLoginButton();
  }
});
