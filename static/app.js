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

  function initBatchDelete() {
    var form = document.getElementById('batch-delete-form');
    if (!form) return;
    var selectAll = document.getElementById('select-all');
    var checks = Array.prototype.slice.call(document.querySelectorAll('.row-check'));
    var countEl = document.getElementById('batch-count');
    var infoEl = document.getElementById('batch-selected');
    var btn = document.getElementById('batch-delete-btn');

    function selected() {
      return checks.filter(function (checkbox) { return checkbox.checked; });
    }

    function refresh() {
      var count = selected().length;
      if (countEl) countEl.textContent = String(count);
      if (infoEl) infoEl.hidden = count === 0;
      if (btn) btn.disabled = count === 0;
      if (selectAll) {
        selectAll.checked = checks.length > 0 && count === checks.length;
        selectAll.indeterminate = count > 0 && count < checks.length;
      }
    }

    checks.forEach(function (checkbox) {
      checkbox.addEventListener('change', refresh);
    });
    if (selectAll) {
      selectAll.addEventListener('change', function () {
        checks.forEach(function (checkbox) {
          checkbox.checked = selectAll.checked;
        });
        refresh();
      });
    }

    form.addEventListener('submit', function (event) {
      var picked = selected();
      if (!picked.length) {
        event.preventDefault();
        return;
      }
      form.querySelectorAll('input[name="student_ids"]').forEach(function (el) {
        el.remove();
      });
      picked.forEach(function (checkbox) {
        var input = document.createElement('input');
        input.type = 'hidden';
        input.name = 'student_ids';
        input.value = checkbox.value;
        form.appendChild(input);
      });
      var label;
      if (picked.length <= 5) {
        label = picked.map(function (checkbox) { return checkbox.getAttribute('data-exam-id'); }).join('、');
      } else {
        label = picked[0].getAttribute('data-exam-id') + ' 等 ' + picked.length + ' 人';
      }
      if (!window.confirm('确定删除考生 ' + label + ' 及其全部提交文件？此操作不可恢复。')) {
        event.preventDefault();
      }
    });

    refresh();
  }

  ready(function () {
    initFileDrops();
    initRipples();
    initCountUp();
    initBatchDelete();
  });
})();
