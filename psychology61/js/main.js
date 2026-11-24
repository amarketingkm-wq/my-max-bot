/* psychology61.ru — меню, cookie, доступность, модалки, карусель сертификатов */
(function () {
  'use strict';

  const CERT_FILES = [
    'images/certificates/cert-1.png',
    'images/certificates/cert-2.png',
    'images/certificates/cert-3.png',
    'images/certificates/cert-4.png',
    'images/certificates/cert-5.png',
    'images/certificates/cert-6.png',
    'images/certificates/cert-7.png',
    'images/certificates/cert-8.png',
    'images/certificates/cert-9.png',
    'images/certificates/cert-10.png',
    'images/certificates/cert-11.png',
    'images/certificates/cert-12.png',
    'images/certificates/cert-13.svg',
    'images/certificates/cert-14.svg',
    'images/certificates/cert-15.svg',
    'images/certificates/cert-16.svg',
    'images/certificates/cert-17.svg',
    'images/certificates/cert-18.svg'
  ];

  function qs(sel, root) {
    return (root || document).querySelector(sel);
  }
  function qsa(sel, root) {
    return Array.from((root || document).querySelectorAll(sel));
  }

  function markCurrentNav() {
    const file = (location.pathname.split('/').pop() || 'index.html').toLowerCase();
    qsa('.menu a').forEach((a) => {
      const href = (a.getAttribute('href') || '').toLowerCase();
      if (href === file || (file === '' && href === 'index.html')) {
        a.setAttribute('aria-current', 'page');
      }
    });
  }

  function initMenu() {
    const toggle = qs('#menuToggle');
    const sidebar = qs('#sidebar');
    if (!toggle || !sidebar) return;

    const close = () => {
      sidebar.classList.remove('open');
      toggle.setAttribute('aria-expanded', 'false');
    };
    const open = () => {
      sidebar.classList.add('open');
      toggle.setAttribute('aria-expanded', 'true');
    };

    toggle.addEventListener('click', () => {
      if (sidebar.classList.contains('open')) close();
      else open();
    });

    document.addEventListener('click', (e) => {
      if (!sidebar.classList.contains('open')) return;
      if (sidebar.contains(e.target) || toggle.contains(e.target)) return;
      close();
    });

    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') close();
    });
  }

  function initAccessibility() {
    const btn = qs('#visionToggle');
    if (!btn) return;
    const KEY = 'psychology61_a11y';
    const apply = (on) => {
      document.body.classList.toggle('accessibility-mode', on);
      btn.setAttribute('aria-pressed', on ? 'true' : 'false');
      try {
        localStorage.setItem(KEY, on ? '1' : '0');
      } catch (_) {}
    };
    let on = false;
    try {
      on = localStorage.getItem(KEY) === '1';
    } catch (_) {}
    apply(on);
    btn.addEventListener('click', () => apply(!document.body.classList.contains('accessibility-mode')));
  }

  function initCookies() {
    const note = qs('#cookieNote');
    if (!note) return;
    const KEY = 'psychology61_cookie';
    let choice = null;
    try {
      choice = localStorage.getItem(KEY);
    } catch (_) {}
    if (!choice) note.classList.add('show');
    qsa('[data-cookie-choice]', note).forEach((btn) => {
      btn.addEventListener('click', () => {
        const value = btn.getAttribute('data-cookie-choice') || 'accepted';
        try {
          localStorage.setItem(KEY, value);
        } catch (_) {}
        note.classList.remove('show');
      });
    });
  }

  window.openModal = function openModal(id) {
    const el = qs('#modal-' + id);
    if (!el) return;
    el.classList.add('active');
    document.body.style.overflow = 'hidden';
  };

  window.closeModal = function closeModal(id) {
    const el = qs('#modal-' + id);
    if (!el) return;
    el.classList.remove('active');
    if (!qs('.modal-overlay.active')) document.body.style.overflow = '';
  };

  function initModals() {
    qsa('.modal-overlay').forEach((overlay) => {
      overlay.addEventListener('click', (e) => {
        if (e.target === overlay) {
          overlay.classList.remove('active');
          if (!qs('.modal-overlay.active')) document.body.style.overflow = '';
        }
      });
    });
    document.addEventListener('keydown', (e) => {
      if (e.key !== 'Escape') return;
      qsa('.modal-overlay.active').forEach((o) => o.classList.remove('active'));
      document.body.style.overflow = '';
    });
  }

  function initCarousel() {
    const track = qs('#carouselTrack');
    const centerImage = qs('#centerImage');
    const centerFrame = qs('#centerFrame');
    if (!track || !centerImage) return;

    let index = 0;
    const n = CERT_FILES.length;

    function srcAt(i) {
      const k = ((i % n) + n) % n;
      return CERT_FILES[k];
    }

    function render() {
      track.innerHTML = '';
      const left = document.createElement('div');
      left.className = 'carousel-item left';
      left.innerHTML = '<img alt="Сертификат" src="' + srcAt(index - 1) + '">';
      left.addEventListener('click', () => {
        index = (index - 1 + n) % n;
        render();
      });

      const right = document.createElement('div');
      right.className = 'carousel-item right';
      right.innerHTML = '<img alt="Сертификат" src="' + srcAt(index + 1) + '">';
      right.addEventListener('click', () => {
        index = (index + 1) % n;
        render();
      });

      track.appendChild(left);
      track.appendChild(right);
      centerImage.src = srcAt(index);
      centerImage.alt = 'Сертификат ' + (index + 1);
    }

    const prev = qs('.prev-btn');
    const next = qs('.next-btn');
    if (prev) prev.addEventListener('click', () => {
      index = (index - 1 + n) % n;
      render();
    });
    if (next) next.addEventListener('click', () => {
      index = (index + 1) % n;
      render();
    });

    const lightbox = qs('#certLightbox');
    const lightboxImage = qs('#lightboxImage');
    const lightboxClose = qs('.lightbox-close');
    function openLightbox() {
      if (!lightbox || !lightboxImage) return;
      lightboxImage.src = srcAt(index);
      lightbox.classList.add('active');
    }
    function closeLightbox() {
      if (!lightbox) return;
      lightbox.classList.remove('active');
    }
    if (centerFrame) centerFrame.addEventListener('click', openLightbox);
    if (lightboxClose) lightboxClose.addEventListener('click', closeLightbox);
    if (lightbox) {
      lightbox.addEventListener('click', (e) => {
        if (e.target === lightbox) closeLightbox();
      });
    }
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') closeLightbox();
    });

    render();
  }

  document.addEventListener('DOMContentLoaded', () => {
    markCurrentNav();
    initMenu();
    initAccessibility();
    initCookies();
    initModals();
    initCarousel();
  });
})();
