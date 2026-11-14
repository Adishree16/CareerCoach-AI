const form = document.querySelector('#account-form');
const email = document.querySelector('#email');
const sendButton = document.querySelector('#send-otp');
const otpBlock = document.querySelector('#otp-block');
const otpInputs = [...document.querySelectorAll('.otp-inputs input')];
const verifyButton = document.querySelector('#verify-otp');
const resendButton = document.querySelector('#resend-otp');
const otpMessage = document.querySelector('#otp-message');
const password = document.querySelector('#password');
const confirmPassword = document.querySelector('#confirm-password');
const passwordMessage = document.querySelector('#password-message');
const passwordErrors = document.querySelector('#password-errors');
const passwordErrorMessages = [...passwordErrors.querySelectorAll('[data-rule-message]')];
const submitMessage = document.querySelector('#submit-message');
const createAccountButton = document.querySelector('#create-account-submit');
let otpInteractionStarted = false;
let otpVerified = false;
let passwordTouched = false;
let resendTimer;

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
  createAccountButton.disabled = !(
    validEmail(email.value) &&
    otpVerified &&
    passwordValid &&
    passwordsMatch
  );
}

// Future authentication integration must pass a genuine backend verification result here.
function applyOtpVerificationResult(verified) {
  otpVerified = verified === true;
  updateCreateAccountButton();
}

function updateEmailState() {
  const isValid = validEmail(email.value);
  sendButton.disabled = !isValid;
  email.setCustomValidity(isValid ? '' : 'Enter a valid email ID');
  if (!isValid) otpMessage.textContent = 'Enter a valid email ID';
  else if (!otpInteractionStarted) otpMessage.textContent = '';
  updateCreateAccountButton();
}

function startCountdown() {
  clearInterval(resendTimer);
  let remaining = 30;
  resendButton.disabled = true;
  const tick = () => {
    resendButton.textContent = `00:${String(remaining).padStart(2, '0')}`;
    if (remaining <= 0) {
      clearInterval(resendTimer);
      resendButton.disabled = false;
      resendButton.textContent = 'Resend OTP';
    }
    remaining -= 1;
  };
  tick();
  resendTimer = setInterval(tick, 1000);
}

function updateVerifyButtonState() {
  verifyButton.disabled = !otpInputs.every((input) => /^\d$/.test(input.value));
}

updateVerifyButtonState();

email.addEventListener('input', updateEmailState);
email.addEventListener('change', updateEmailState);
updateEmailState();
sendButton.addEventListener('click', () => {
  updateEmailState();
  if (!validEmail(email.value)) {
    email.reportValidity();
    return;
  }
  otpInteractionStarted = true;
  otpBlock.hidden = false;
  otpMessage.textContent = 'Actual OTP delivery will be available after backend integration.';
  startCountdown();
  otpInputs[0].focus();
});

resendButton.addEventListener('click', () => {
  if (resendButton.disabled) return;
  otpMessage.textContent = 'Resend is a frontend preview; OTP delivery is not connected yet.';
  startCountdown();
});

otpInputs.forEach((input, index) => {
  input.addEventListener('input', () => {
    const digits = input.value.replace(/\D/g, '');
    input.value = digits.slice(-1);
    updateVerifyButtonState();
    if (input.value && index < otpInputs.length - 1) otpInputs[index + 1].focus();
  });
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Backspace' && !input.value && index > 0) otpInputs[index - 1].focus();
    if (event.key === 'ArrowLeft' && index > 0) otpInputs[index - 1].focus();
    if (event.key === 'ArrowRight' && index < otpInputs.length - 1) otpInputs[index + 1].focus();
  });
  input.addEventListener('paste', (event) => {
    const pasted = event.clipboardData.getData('text').replace(/\D/g, '').slice(0, otpInputs.length);
    if (!pasted) return;
    event.preventDefault();
    pasted.split('').forEach((digit, digitIndex) => { otpInputs[digitIndex].value = digit; });
    updateVerifyButtonState();
    otpInputs[Math.min(pasted.length, otpInputs.length - 1)].focus();
  });
});

verifyButton.addEventListener('click', () => {
  if (verifyButton.disabled) return;
  otpMessage.textContent = 'OTP verification will be available after backend integration.';
});

document.querySelectorAll('.visibility-toggle').forEach((button) => {
  button.addEventListener('click', () => {
    const field = button.parentElement.querySelector('input');
    const showing = field.type === 'password';
    field.type = showing ? 'text' : 'password';
    button.setAttribute('aria-pressed', String(showing));
    button.setAttribute('aria-label', `${showing ? 'Hide' : 'Show'} ${field.id === 'password' ? 'password' : 'confirm password'}`);
  });
});

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
updatePasswordRules();

form.addEventListener('submit', (event) => {
  event.preventDefault();
  submitMessage.textContent = '';
  passwordMessage.textContent = '';
  updateEmailState();
  const passwordValid = updatePasswordRules();
  const otpComplete = otpInputs.every((input) => /^\d$/.test(input.value));
  const passwordsMatch = password.value === confirmPassword.value;

  if (!validEmail(email.value)) {
    email.setCustomValidity('Enter a valid email ID');
    email.reportValidity();
    return;
  }
  if (!otpInteractionStarted) {
    submitMessage.textContent = 'Select Send OTP to begin the email verification step.';
    sendButton.focus();
    return;
  }
  if (!otpComplete) {
    submitMessage.textContent = 'Enter all six OTP digits to continue.';
    otpInputs.find((input) => !input.value)?.focus();
    return;
  }
  if (!otpVerified) {
    submitMessage.textContent = 'OTP verification will be available after backend integration.';
    verifyButton.focus();
    return;
  }
  if (!passwordValid || !passwordsMatch) {
    passwordMessage.textContent = !passwordsMatch ? 'Passwords do not match.' : 'Meet all password requirements to continue.';
    (!passwordValid ? password : confirmPassword).focus();
    return;
  }
  submitMessage.textContent = 'Account creation will be available after backend integration.';
});
