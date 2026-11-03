// Applies the remembered theme before first paint (classic script, runs synchronously in <head>).
(function () {
  try {
    var t = window.localStorage.getItem('earmark.theme');
    if (t === 'light' || t === 'dark') document.documentElement.setAttribute('data-theme', t);
  } catch (e) { /* storage unavailable: follow the system theme */ }
})();
