(function () {
  "use strict";
  var root = document.documentElement;
  var reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;

  var toggle = document.querySelector(".theme-toggle");
  if (toggle) {
    toggle.addEventListener("click", function () {
      var dark = root.dataset.theme !== "dark";
      if (dark) root.dataset.theme = "dark"; else delete root.dataset.theme;
      try { localStorage.setItem("theme", dark ? "dark" : "light"); } catch (e) {}
    });
  }

  document.querySelectorAll(".content pre").forEach(function (pre) {
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "copy-btn";
    btn.textContent = "コピー";
    btn.addEventListener("click", function () {
      var code = pre.querySelector("code") || pre;
      var done = function () {
        btn.textContent = "コピーしました";
        btn.classList.add("is-done");
        setTimeout(function () { btn.textContent = "コピー"; btn.classList.remove("is-done"); }, 1800);
      };
      if (navigator.clipboard) {
        navigator.clipboard.writeText(code.innerText).then(done, function () {});
      } else {
        var range = document.createRange();
        range.selectNodeContents(code);
        var sel = getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
        try { document.execCommand("copy"); done(); } catch (e) {}
        sel.removeAllRanges();
      }
    });
    pre.appendChild(btn);
  });

  var bar = document.querySelector(".progress span");
  var toTop = document.querySelector(".to-top");
  var ticking = false;
  function onScroll() {
    ticking = false;
    var max = root.scrollHeight - root.clientHeight;
    var y = root.scrollTop || document.body.scrollTop;
    if (bar) bar.style.setProperty("--p", max > 0 ? Math.min(1, y / max) : 0);
    if (toTop) toTop.classList.toggle("is-shown", y > 600);
  }
  addEventListener("scroll", function () {
    if (!ticking) { ticking = true; requestAnimationFrame(onScroll); }
  }, { passive: true });
  onScroll();
  if (toTop) toTop.addEventListener("click", function () { scrollTo({ top: 0, behavior: reduceMotion ? "auto" : "smooth" }); });

  var reveals = document.querySelectorAll(".reveal");
  if ("IntersectionObserver" in window && !reduceMotion) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) { e.target.classList.add("is-visible"); io.unobserve(e.target); }
      });
    }, { rootMargin: "0px 0px -8% 0px" });
    reveals.forEach(function (el, i) {
      el.style.transitionDelay = (i % 3) * 80 + "ms";
      io.observe(el);
    });
  } else {
    reveals.forEach(function (el) { el.classList.add("is-visible"); });
  }

  // Number cards count up to their value when they scroll into view ("2.4倍" → 0.0 … 2.4).
  var stats = document.querySelectorAll(".stat-value");
  if (stats.length && "IntersectionObserver" in window && !reduceMotion) {
    var countIo = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (!e.isIntersecting) return;
        countIo.unobserve(e.target);
        countUp(e.target);
      });
    }, { rootMargin: "0px 0px -10% 0px" });
    stats.forEach(function (el) { if (/^\D*\d+(\.\d+)?\D*$/.test(el.textContent.trim())) countIo.observe(el); });
  }

  function countUp(el) {
    var text = el.textContent.trim();
    var m = text.match(/\d+(\.\d+)?/);
    var target = parseFloat(m[0]), decimals = m[1] ? m[1].length - 1 : 0;
    var before = text.slice(0, m.index), after = text.slice(m.index + m[0].length);
    var start = performance.now(), duration = 1100;
    function frame(now) {
      var t = Math.min(1, (now - start) / duration);
      var eased = 1 - Math.pow(1 - t, 3);
      el.textContent = before + (target * eased).toFixed(decimals) + after;
      if (t < 1) requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
  }

  var tocLinks = document.querySelectorAll(".toc a");
  if (tocLinks.length && "IntersectionObserver" in window) {
    var byId = {};
    tocLinks.forEach(function (a) { byId[decodeURIComponent(a.hash.slice(1))] = a; });
    var headings = document.querySelectorAll(".content h2[id], .content h3[id]");
    var tocIo = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (!e.isIntersecting) return;
        tocLinks.forEach(function (a) { a.classList.remove("is-active"); });
        var a = byId[e.target.id];
        if (a) a.classList.add("is-active");
      });
    }, { rootMargin: "-80px 0px -70% 0px" });
    headings.forEach(function (h) { tocIo.observe(h); });
    if (matchMedia("(max-width: 900px)").matches) {
      var d = document.querySelector(".toc details");
      if (d) d.open = false;
    }
  }

  var mascot = document.querySelector(".hero-mascot");
  if (mascot) {
    var messages = JSON.parse(mascot.dataset.messages || "[]");
    var bubble = mascot.querySelector(".hero-bubble");
    var bubbleText = bubble.querySelector(".hero-bubble-text");
    var btn = mascot.querySelector(".hero-mascot-btn");
    var current = 0, timer = null;
    var say = function (i) {
      current = (i + messages.length) % messages.length;
      bubble.classList.add("is-changing");
      setTimeout(function () {
        bubbleText.textContent = messages[current];
        bubble.classList.remove("is-changing");
      }, reduceMotion ? 0 : 220);
    };
    var schedule = function () {
      clearInterval(timer);
      if (!reduceMotion && messages.length > 1) timer = setInterval(function () { say(current + 1); }, 8000);
    };
    btn.addEventListener("click", function () {
      say(current + 1);
      btn.classList.remove("is-jumping");
      void btn.offsetWidth;
      btn.classList.add("is-jumping");
      schedule();
    });
    btn.addEventListener("animationend", function () { btn.classList.remove("is-jumping"); });
    schedule();
  }

  var canvas = document.querySelector(".hero-canvas");
  if (canvas && canvas.getContext) molecules(canvas);

  // Floating "molecules": nodes drift slowly and bond with nearby nodes.
  function molecules(canvas) {
    var ctx = canvas.getContext("2d");
    var dpr = Math.min(window.devicePixelRatio || 1, 2);
    var w = 0, h = 0, nodes = [], running = false, pointer = null, inView = true;
    var symbols = ["H", "C", "O", "N", "e⁻", "π", "∑", "λ"];

    function resize() {
      var r = canvas.getBoundingClientRect();
      w = r.width; h = r.height;
      canvas.width = w * dpr; canvas.height = h * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      var count = Math.round(Math.min(60, Math.max(18, w * h / 9000)));
      nodes = [];
      for (var i = 0; i < count; i++) {
        nodes.push({
          x: Math.random() * w, y: Math.random() * h,
          vx: (Math.random() - .5) * .35, vy: (Math.random() - .5) * .35,
          r: 1.5 + Math.random() * 2.5,
          s: Math.random() < .18 ? symbols[i % symbols.length] : null
        });
      }
    }

    function frame() {
      ctx.clearRect(0, 0, w, h);
      var link = Math.min(140, w / 5);
      for (var i = 0; i < nodes.length; i++) {
        var a = nodes[i];
        if (!reduceMotion) {
          a.x += a.vx; a.y += a.vy;
          if (a.x < 0 || a.x > w) a.vx *= -1;
          if (a.y < 0 || a.y > h) a.vy *= -1;
          if (pointer) {
            var px = pointer.x - a.x, py = pointer.y - a.y, pd = Math.hypot(px, py);
            if (pd < 160 && pd > 1) { a.x += px / pd * .25; a.y += py / pd * .25; }
          }
        }
        for (var j = i + 1; j < nodes.length; j++) {
          var b = nodes[j], d = Math.hypot(a.x - b.x, a.y - b.y);
          if (d < link) {
            ctx.strokeStyle = "rgba(255,255,255," + (1 - d / link) * .35 + ")";
            ctx.lineWidth = 1;
            ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
          }
        }
      }
      for (var k = 0; k < nodes.length; k++) {
        var n = nodes[k];
        if (n.s) {
          ctx.fillStyle = "rgba(255,255,255,.55)";
          ctx.font = "600 13px Georgia, serif";
          ctx.textAlign = "center"; ctx.textBaseline = "middle";
          ctx.fillText(n.s, n.x, n.y);
        } else {
          ctx.fillStyle = "rgba(255,255,255,.75)";
          ctx.beginPath(); ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2); ctx.fill();
        }
      }
      if (running) requestAnimationFrame(frame);
    }

    function start() { if (!running && !reduceMotion) { running = true; requestAnimationFrame(frame); } }
    function stop() { running = false; }

    resize();
    frame();
    addEventListener("resize", function () { resize(); if (!running) frame(); });
    var hero = canvas.parentElement;
    hero.addEventListener("pointermove", function (e) {
      var r = canvas.getBoundingClientRect();
      pointer = { x: e.clientX - r.left, y: e.clientY - r.top };
    });
    hero.addEventListener("pointerleave", function () { pointer = null; });
    if ("IntersectionObserver" in window) {
      new IntersectionObserver(function (es) {
        inView = es[0].isIntersecting;
        inView ? start() : stop();
      }).observe(canvas);
    } else {
      start();
    }
    document.addEventListener("visibilitychange", function () { document.hidden || !inView ? stop() : start(); });
  }
})();
