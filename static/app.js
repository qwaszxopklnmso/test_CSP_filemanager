(function () {
  'use strict';

  var reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  function ready(fn) {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', fn);
    } else {
      fn();
    }
  }

  function initFileDrops() {
    document.querySelectorAll('.file-drop').forEach(function (drop) {
      var input = drop.querySelector('input[type="file"]');
      var chip = drop.querySelector('[data-file-name]');
      if (!input) return;
      var defaultText = chip ? chip.textContent : '未选择文件';

      function refresh() {
        if (input.files && input.files.length) {
          drop.classList.add('has-file');
          if (chip) chip.textContent = input.files[0].name;
        } else {
          drop.classList.remove('has-file');
          if (chip) chip.textContent = defaultText;
        }
      }

      input.addEventListener('change', refresh);

      ['dragenter', 'dragover'].forEach(function (name) {
        drop.addEventListener(name, function (event) {
          if (input.disabled) return;
          event.preventDefault();
          event.stopPropagation();
          drop.classList.add('dragging');
        });
      });

      ['dragleave', 'dragend'].forEach(function (name) {
        drop.addEventListener(name, function () {
          drop.classList.remove('dragging');
        });
      });

      drop.addEventListener('drop', function (event) {
        if (input.disabled) return;
        event.preventDefault();
        event.stopPropagation();
        drop.classList.remove('dragging');
        var files = event.dataTransfer && event.dataTransfer.files;
        if (files && files.length) {
          try {
            input.files = files;
          } catch (err) {
            return;
          }
          refresh();
        }
      });
    });
  }

  function initRipples() {
    if (reduceMotion) return;
    document.addEventListener('click', function (event) {
      var button = event.target.closest ? event.target.closest('.btn') : null;
      if (!button || button.disabled) return;
      var rect = button.getBoundingClientRect();
      var size = Math.max(rect.width, rect.height) * 1.15;
      var ripple = document.createElement('span');
      ripple.className = 'ripple';
      ripple.style.width = size + 'px';
      ripple.style.height = size + 'px';
      ripple.style.left = (event.clientX - rect.left - size / 2) + 'px';
      ripple.style.top = (event.clientY - rect.top - size / 2) + 'px';
      button.appendChild(ripple);
      window.setTimeout(function () {
        ripple.remove();
      }, 650);
    });
  }

  function initCountUp() {
    document.querySelectorAll('.stat-num').forEach(function (el) {
      var target = parseInt((el.textContent || '').replace(/[^0-9]/g, ''), 10);
      if (isNaN(target) || target <= 0 || target > 1000000) return;
      if (reduceMotion) return;
      var start = null;
      var duration = 850;
      function step(ts) {
        if (start === null) start = ts;
        var progress = Math.min((ts - start) / duration, 1);
        var eased = 1 - Math.pow(1 - progress, 3);
        el.textContent = String(Math.round(target * eased));
        if (progress < 1) window.requestAnimationFrame(step);
      }
      el.textContent = '0';
      window.requestAnimationFrame(step);
    });
  }

  ready(function () {
    initFileDrops();
    initRipples();
    initCountUp();
  });
})();
