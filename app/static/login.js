// Login page (served by /login). Lives in a file, not an inline <script>, so
// the Content-Security-Policy can refuse every inline script (roadmap §1.13).
(function () {
'use strict';

const form = document.getElementById('login-form');
const submitBtn = document.getElementById('submit-btn');
const errorBox = document.getElementById('error-box');
const langPills = document.getElementById('lang-pills');
const SUPPORTED_UI_LANGUAGES = ['en', 'fa', 'es', 'ar'];
const RTL_LANGUAGES = new Set(['fa', 'ar']);
let currentLanguage = 'en';
let loginPassword = '';  // set after a default-password login (see below)
// Second step of a sign-in with two-factor on: the server returned a
// short-lived challenge instead of a session.
const tfa = { challenge: null, recovery: false };
function renderTfaMode() {
  document.getElementById('tfa-sub').textContent = t(tfa.recovery ? 'tfaSubRecovery' : 'tfaSub');
  document.getElementById('tfa-code-label').textContent = t(tfa.recovery ? 'tfaRecoveryCode' : 'tfaCode');
  document.getElementById('tfa-recovery-toggle').textContent = t(tfa.recovery ? 'tfaUseApp' : 'tfaUseRecovery');
  const input = document.getElementById('tfa-code');
  input.setAttribute('inputmode', tfa.recovery ? 'text' : 'numeric');
  input.setAttribute('autocomplete', tfa.recovery ? 'off' : 'one-time-code');
}
const I18N = {
  en: {
    title: 'Accounting Assistant',
    subtitle: 'Sign in to access transactions, reports, and settings.',
    language: 'Language',
    username: 'Username',
    password: 'Password',
    showPassword: 'Show password',
    hidePassword: 'Hide password',
    login: 'Login',
    signingIn: 'Signing in...',
    tfaTitle: "Two-factor sign-in",
    tfaSub: "Enter the six-digit code from your authenticator app.",
    tfaSubRecovery: "Enter one of your recovery codes. Each works once.",
    tfaCode: "Code",
    tfaRecoveryCode: "Recovery code",
    tfaVerify: "Verify",
    tfaUseRecovery: "Use a recovery code",
    tfaUseApp: "Use the app code",
    tfaBack: "Back",
    tfaNeedCode: "Enter the code.",
    tfaRecoveryLow: "You signed in with a recovery code — {n} left. Make new ones under Two-factor sign-in.",
    changeTitle: 'Set a new password',
    changeSub: 'You signed in with the default password. Choose a new one to continue.',
    newPassword: 'New password',
    currentPassword: 'Current password',
    currentPasswordRequired: 'Enter your current password.',
    confirmPassword: 'Confirm password',
    savePassword: 'Save password',
    passwordMismatch: 'The two passwords do not match.',
    passwordShort: 'Use at least 8 characters.',
    required: 'Username and password are required.',
    failed: 'Login failed.',
    connectionError: 'Connection error',
  },
  fa: {
    title: 'دستیار حسابداری',
    subtitle: 'برای دسترسی به اسناد، گزارش‌ها و تنظیمات وارد شوید.',
    language: 'زبان',
    username: 'نام کاربری',
    password: 'رمز عبور',
    showPassword: 'نمایش رمز',
    hidePassword: 'پنهان کردن رمز',
    login: 'ورود',
    signingIn: 'در حال ورود...',
    tfaTitle: "ورود دومرحله‌ای",
    tfaSub: "کد شش‌رقمی برنامهٔ احراز هویت را وارد کنید.",
    tfaSubRecovery: "یکی از کدهای بازیابی را وارد کنید. هر کد یک بار کار می‌کند.",
    tfaCode: "کد",
    tfaRecoveryCode: "کد بازیابی",
    tfaVerify: "تأیید",
    tfaUseRecovery: "استفاده از کد بازیابی",
    tfaUseApp: "استفاده از کد برنامه",
    tfaBack: "بازگشت",
    tfaNeedCode: "کد را وارد کنید.",
    tfaRecoveryLow: "با کد بازیابی وارد شدید — {n} کد باقی مانده. از بخش ورود دومرحله‌ای کدهای جدید بسازید.",
    changeTitle: 'رمز عبور جدید تعیین کنید',
    changeSub: 'با رمز پیش‌فرض وارد شدید. برای ادامه یک رمز جدید انتخاب کنید.',
    newPassword: 'رمز عبور جدید',
    currentPassword: 'رمز عبور فعلی',
    currentPasswordRequired: 'رمز عبور فعلی را وارد کنید.',
    confirmPassword: 'تکرار رمز عبور',
    savePassword: 'ذخیرهٔ رمز',
    passwordMismatch: 'دو رمز با هم یکی نیستند.',
    passwordShort: 'حداقل ۸ کاراکتر وارد کنید.',
    required: 'نام کاربری و رمز عبور الزامی است.',
    failed: 'ورود ناموفق بود.',
    connectionError: 'خطای اتصال',
  },
  es: {
    title: 'Asistente Contable',
    subtitle: 'Inicia sesión para acceder a transacciones, reportes y configuración.',
    language: 'Idioma',
    username: 'Usuario',
    password: 'Contraseña',
    showPassword: 'Mostrar contraseña',
    hidePassword: 'Ocultar contraseña',
    login: 'Entrar',
    signingIn: 'Ingresando...',
    tfaTitle: "Verificación en dos pasos",
    tfaSub: "Introduce el código de seis dígitos de tu app de autenticación.",
    tfaSubRecovery: "Introduce uno de tus códigos de recuperación. Cada uno sirve una vez.",
    tfaCode: "Código",
    tfaRecoveryCode: "Código de recuperación",
    tfaVerify: "Verificar",
    tfaUseRecovery: "Usar un código de recuperación",
    tfaUseApp: "Usar el código de la app",
    tfaBack: "Volver",
    tfaNeedCode: "Introduce el código.",
    tfaRecoveryLow: "Entraste con un código de recuperación: quedan {n}. Crea nuevos en Verificación en dos pasos.",
    changeTitle: 'Establece una nueva contraseña',
    changeSub: 'Has iniciado sesión con la contraseña por defecto. Elige una nueva para continuar.',
    newPassword: 'Nueva contraseña',
    currentPassword: 'Contraseña actual',
    currentPasswordRequired: 'Introduce tu contraseña actual.',
    confirmPassword: 'Confirmar contraseña',
    savePassword: 'Guardar contraseña',
    passwordMismatch: 'Las contraseñas no coinciden.',
    passwordShort: 'Usa al menos 8 caracteres.',
    required: 'Usuario y contraseña son obligatorios.',
    failed: 'Error de inicio de sesión.',
    connectionError: 'Error de conexión',
  },
  ar: {
    title: 'مساعد المحاسبة',
    subtitle: 'سجل الدخول للوصول إلى القيود والتقارير والإعدادات.',
    language: 'اللغة',
    username: 'اسم المستخدم',
    password: 'كلمة المرور',
    showPassword: 'إظهار كلمة المرور',
    hidePassword: 'إخفاء كلمة المرور',
    login: 'تسجيل الدخول',
    signingIn: 'جارٍ تسجيل الدخول...',
    tfaTitle: "التحقق بخطوتين",
    tfaSub: "أدخل الرمز المكوّن من ستة أرقام من تطبيق المصادقة.",
    tfaSubRecovery: "أدخل أحد رموز الاسترداد. كل رمز يعمل مرة واحدة.",
    tfaCode: "الرمز",
    tfaRecoveryCode: "رمز الاسترداد",
    tfaVerify: "تحقّق",
    tfaUseRecovery: "استخدام رمز استرداد",
    tfaUseApp: "استخدام رمز التطبيق",
    tfaBack: "رجوع",
    tfaNeedCode: "أدخل الرمز.",
    tfaRecoveryLow: "دخلت برمز استرداد — تبقّى {n}. أنشئ رموزًا جديدة من التحقق بخطوتين.",
    changeTitle: 'عيّن كلمة مرور جديدة',
    changeSub: 'سجّلت الدخول بكلمة المرور الافتراضية. اختر كلمة جديدة للمتابعة.',
    newPassword: 'كلمة المرور الجديدة',
    currentPassword: 'كلمة المرور الحالية',
    currentPasswordRequired: 'أدخل كلمة المرور الحالية.',
    confirmPassword: 'تأكيد كلمة المرور',
    savePassword: 'حفظ كلمة المرور',
    passwordMismatch: 'كلمتا المرور غير متطابقتين.',
    passwordShort: 'استخدم 8 أحرف على الأقل.',
    required: 'اسم المستخدم وكلمة المرور مطلوبان.',
    failed: 'فشل تسجيل الدخول.',
    connectionError: 'خطأ في الاتصال',
  },
};

function t(key) {
  const dict = I18N[currentLanguage] || I18N.en;
  return dict[key] || I18N.en[key] || key;
}

function applyLanguage(lang, persist = true) {
  const nextLang = SUPPORTED_UI_LANGUAGES.includes(lang) ? lang : 'en';
  currentLanguage = nextLang;
  if (persist) localStorage.setItem('aa_ui_language', nextLang);
  document.documentElement.lang = nextLang;
  document.documentElement.dir = RTL_LANGUAGES.has(nextLang) ? 'rtl' : 'ltr';
  document.querySelectorAll('.lang-pill').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.lang === nextLang);
  });
  document.getElementById('login-title').textContent = t('title');
  document.getElementById('login-subtitle').textContent = t('subtitle');
  document.getElementById('login-language-label').textContent = t('language');
  document.getElementById('username-label').textContent = t('username');
  document.getElementById('password-label').textContent = t('password');
  document.getElementById('change-title').textContent = t('changeTitle');
  document.getElementById('change-sub').textContent = t('changeSub');
  document.getElementById('new-password-label').textContent = t('newPassword');
  document.getElementById('confirm-password-label').textContent = t('confirmPassword');
  document.getElementById('change-btn').textContent = t('savePassword');
  document.getElementById('current-password-label').textContent = t('currentPassword');
  submitBtn.textContent = t('login');
  document.getElementById('tfa-title').textContent = t('tfaTitle');
  document.getElementById('tfa-btn').textContent = t('tfaVerify');
  document.getElementById('tfa-back').textContent = t('tfaBack');
  renderTfaMode();
  const pwBtn = document.getElementById('pw-toggle');
  const pwInput = document.getElementById('password');
  if (pwBtn && pwInput) {
    pwBtn.setAttribute('aria-label', t(pwInput.type === 'password' ? 'showPassword' : 'hidePassword'));
  }
}

// Password show/hide toggle.
(function () {
  const btn = document.getElementById('pw-toggle');
  const input = document.getElementById('password');
  if (!btn || !input) return;
  const EYE = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>';
  const EYE_OFF = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>';
  btn.addEventListener('click', function () {
    const show = input.type === 'password';
    input.type = show ? 'text' : 'password';
    btn.setAttribute('aria-pressed', show ? 'true' : 'false');
    btn.setAttribute('aria-label', t(show ? 'hidePassword' : 'showPassword'));
    btn.innerHTML = show ? EYE_OFF : EYE;
  });
})();

function setError(msg) {
  if (!msg) {
    errorBox.style.display = 'none';
    errorBox.textContent = '';
    return;
  }
  errorBox.textContent = msg;
  errorBox.style.display = 'block';
}

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  setError('');
  const username = document.getElementById('username').value.trim();
  const password = document.getElementById('password').value;
  if (!username || !password) {
    setError(t('required'));
    return;
  }
  submitBtn.disabled = true;
  submitBtn.textContent = t('signingIn');
  try {
    const res = await fetch('/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      setError(data.detail || t('failed'));
      return;
    }
    if (data.two_factor_required && data.challenge) {
      tfa.challenge = data.challenge;
      tfa.recovery = false;
      loginPassword = password;  // for the default-password change form after the code
      form.style.display = 'none';
      document.getElementById('tfa-form').style.display = '';
      document.getElementById('tfa-code').value = '';
      setTfaError('');
      renderTfaMode();
      document.getElementById('tfa-code').focus();
      return;
    }
    afterSignIn(data, password);
  } catch (err) {
    setError(t('connectionError') + ': ' + err.message);
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = t('login');
  }
});

// A session is open: remember the language, then either the forced
// password change (default password) or the app.
function afterSignIn(data, password) {
  if (data.user && data.user.preferred_language) {
    localStorage.setItem('aa_ui_language', data.user.preferred_language);
  }
  if (data.two_factor_method === 'recovery') {
    try { sessionStorage.setItem('aa_tfa_recovery_left', String(data.recovery_codes_left)); } catch (_) { /* optional */ }
  }
  if (data.must_change_password) {
    // Locked session: every API call is refused until a real password
    // is set, so swap the login card for the change form right here.
    // The password just typed proves possession — carry it over so the
    // user is not asked twice.
    loginPassword = password;
    form.style.display = 'none';
    document.getElementById('tfa-form').style.display = 'none';
    document.getElementById('change-form').style.display = '';
    const cur = document.getElementById('current-password');
    cur.value = loginPassword;
    cur.style.display = 'none';
    document.getElementById('current-password-label').style.display = 'none';
    document.getElementById('new-password').focus();
    return;
  }
  window.location.href = '/';
}

function setTfaError(msg) {
  const el = document.getElementById('tfa-error');
  el.textContent = msg || '';
  el.style.display = msg ? 'block' : 'none';
}
function backToPassword() {
  tfa.challenge = null;
  document.getElementById('tfa-form').style.display = 'none';
  form.style.display = '';
  document.getElementById('password').value = '';
  document.getElementById('password').focus();
}
document.getElementById('tfa-recovery-toggle').addEventListener('click', () => {
  tfa.recovery = !tfa.recovery;
  document.getElementById('tfa-code').value = '';
  setTfaError('');
  renderTfaMode();
  document.getElementById('tfa-code').focus();
});
document.getElementById('tfa-back').addEventListener('click', backToPassword);
document.getElementById('tfa-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  setTfaError('');
  const code = document.getElementById('tfa-code').value.trim();
  if (!code) { setTfaError(t('tfaNeedCode')); return; }
  const btn = document.getElementById('tfa-btn');
  btn.disabled = true;
  try {
    const res = await fetch('/auth/login/2fa', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ challenge: tfa.challenge, code }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      setTfaError(data.detail || t('failed'));
      // The challenge is gone (timed out, password changed): start over.
      if (res.status === 401 && /timed out/i.test(String(data.detail || ''))) setTimeout(backToPassword, 1500);
      return;
    }
    afterSignIn(data, loginPassword);
  } catch (err) {
    setTfaError(t('connectionError') + ': ' + err.message);
  } finally {
    btn.disabled = false;
  }
});

document.getElementById('change-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const errEl = document.getElementById('change-error');
  errEl.textContent = '';
  const cur = document.getElementById('current-password').value || loginPassword;
  const p1 = document.getElementById('new-password').value;
  const p2 = document.getElementById('confirm-password').value;
  if (!cur) { errEl.textContent = t('currentPasswordRequired'); return; }
  if (p1.length < 8) { errEl.textContent = t('passwordShort'); return; }
  if (p1 !== p2) { errEl.textContent = t('passwordMismatch'); return; }
  const btn = document.getElementById('change-btn');
  btn.disabled = true;
  try {
    const res = await fetch('/auth/change-password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ current_password: cur, password: p1 }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) { errEl.textContent = data.detail || t('failed'); return; }
    window.location.href = '/';
  } catch (err) {
    errEl.textContent = t('connectionError') + ': ' + err.message;
  } finally {
    btn.disabled = false;
  }
});
if (new URLSearchParams(location.search).get('change') === '1') {
  form.style.display = 'none';
  document.getElementById('change-form').style.display = '';
}

langPills.addEventListener('click', (e) => {
  const btn = e.target.closest('.lang-pill[data-lang]');
  if (!btn) return;
  applyLanguage(btn.dataset.lang, true);
});
applyLanguage(localStorage.getItem('aa_ui_language') || 'en', false);

// Web fonts load without blocking render (media="print" until now).
document.querySelectorAll('link[data-async-css]').forEach((l) => { l.media = 'all'; });
})();
