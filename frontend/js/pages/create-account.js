import { apiPost } from '../services/api.js';

const form = document.querySelector('#account-form');
const email = document.querySelector('#email');
const password = document.querySelector('#password');
const confirmPassword = document.querySelector('#confirm-password');
const passwordMessage = document.querySelector('#password-message');
const passwordErrors = document.querySelector('#password-errors');
const passwordErrorMessages = [...passwordErrors.querySelectorAll('[data-rule-message]')];
const submitMessage = document.querySelector('#submit-message');
const createAccountButton = document.querySelector('#create-account-submit');
let passwordTouched = false;
let registrationInProgress = false;
let registrationRedirecting = false;

function validEmail(value) {
  return /^[A-Z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Z0-9!#$%&'*+/=?^_`{|}~-]+)*@(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,}$/i.test(value.trim());
}

function getPasswordRules() {
  return {
    length: password.value.length >= 8,
    uppercase: /[A-Z]/.test(password.value),
    lowercase: /[a-z]/.test(password.value),
    number: /[0-9]/.test(password.value),
    special: /[^A-Za-z0-9\s]/.test(password.value),
  };
}

function updateCreateAccountButton() {
  const passwordValid = Object.values(getPasswordRules()).every(Boolean);
  const passwordsMatch = confirmPassword.value.length > 0 && password.value === confirmPassword.value;
  createAccountButton.disabled = registrationInProgress || registrationRedirecting || !(
    validEmail(email.value) && passwordValid && passwordsMatch
  );
}

function setButtonLabel(label) {
  const textNode = [...createAccountButton.childNodes].find((node) => node.nodeType === Node.TEXT_NODE);
  if (textNode) {
    const leadingWhitespace = textNode.textContent.match(/^\s*/)?.[0] || '';
    textNode.textContent = `${leadingWhitespace}${label} `;
  }
}

function updatePasswordRules() {
  const rules = getPasswordRules();
  passwordErrorMessages.forEach((message) => {
    message.hidden = !passwordTouched || rules[message.dataset.ruleMessage];
  });
  passwordErrors.hidden = !passwordTouched || Object.values(rules).every(Boolean);
  passwordMessage.textContent = confirmPassword.value && password.value !== confirmPassword.value ? 'Passwords do not match.' : '';
  updateCreateAccountButton();
  return Object.values(rules).every(Boolean);
}

email.addEventListener('input', updateCreateAccountButton);
email.addEventListener('change', updateCreateAccountButton);
password.addEventListener('input', () => {
  passwordTouched = true;
  updatePasswordRules();
});
confirmPassword.addEventListener('input', updatePasswordRules);
password.addEventListener('change', () => {
  passwordTouched = true;
  updatePasswordRules();
});
confirmPassword.addEventListener('change', updatePasswordRules);

document.querySelectorAll('.visibility-toggle').forEach((button) => {
  button.addEventListener('click', () => {
    const field = button.parentElement.querySelector('input');
    const showing = field.type === 'password';
    field.type = showing ? 'text' : 'password';
    button.setAttribute('aria-pressed', String(showing));
    button.setAttribute('aria-label', `${showing ? 'Hide' : 'Show'} ${field.id === 'password' ? 'password' : 'confirm password'}`);
  });
});

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (registrationInProgress) return;

  submitMessage.textContent = '';
  passwordMessage.textContent = '';
  passwordTouched = true;
  const passwordValid = updatePasswordRules();
  const passwordsMatch = password.value === confirmPassword.value;
  if (!validEmail(email.value)) {
    email.setCustomValidity('Enter a valid email ID');
    email.reportValidity();
    updateCreateAccountButton();
    return;
  }
  email.setCustomValidity('');
  if (!passwordValid || !passwordsMatch) {
    passwordMessage.textContent = passwordsMatch ? 'Meet all password requirements to continue.' : 'Passwords do not match.';
    (!passwordValid ? password : confirmPassword).focus();
    updateCreateAccountButton();
    return;
  }

  registrationInProgress = true;
  updateCreateAccountButton();
  createAccountButton.setAttribute('aria-busy', 'true');
  setButtonLabel('Creating Account...');
  try {
    const result = await apiPost('/api/auth/register', {
      email: email.value.trim(),
      password: password.value,
    });
    if (result?.registered === true && result?.authenticated === true) {
      submitMessage.textContent = 'Account created successfully. Please log in with your email and password.';
    } else if (result?.registered === true && result?.confirmation_required === true) {
      submitMessage.textContent = result.message || 'Your account was created. Additional verification is required before you can sign in.';
    } else {
      throw new Error('Registration was not completed.');
    }
    registrationRedirecting = true;
    updateCreateAccountButton();
    window.setTimeout(() => {
      window.location.href = 'login.html';
    }, 1500);
  } catch {
    submitMessage.textContent = 'Unable to create your account right now. Please try again later.';
  } finally {
    registrationInProgress = false;
    createAccountButton.setAttribute('aria-busy', 'false');
      setButtonLabel('Create Account');
    updateCreateAccountButton();
  }
});

updatePasswordRules();
updateCreateAccountButton();
