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

  // Flip cards turn over between the paper's wording and an analogy.
  document.querySelectorAll(".flip").forEach(function (card) {
    card.addEventListener("click", function () {
      var flipped = card.classList.toggle("is-flipped");
      card.setAttribute("aria-pressed", String(flipped));
    });
  });

  // Bar charts: widths relative to the largest value, grown when they scroll into view.
  var charts = document.querySelectorAll(".bars");
  charts.forEach(function (chart) {
    var bars = chart.querySelectorAll(".bar");
    var values = Array.prototype.map.call(bars, function (b) { return parseFloat(b.dataset.value) || 0; });
    var max = Math.max.apply(null, values.concat([0]));
    bars.forEach(function (b, i) { b.style.setProperty("--w", (max > 0 ? Math.max(2, values[i] / max * 100) : 0) + "%"); });
  });
  if (charts.length && "IntersectionObserver" in window && !reduceMotion) {
    var barIo = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) { e.target.classList.add("is-grown"); barIo.unobserve(e.target); }
      });
    }, { rootMargin: "0px 0px -15% 0px" });
    charts.forEach(function (c) { barIo.observe(c); });
  } else {
    charts.forEach(function (c) { c.classList.add("is-grown"); });
  }

  // Step diagrams light up one step after another while they are on screen.
  var flows = document.querySelectorAll(".flow");
  if (flows.length && "IntersectionObserver" in window && !reduceMotion) {
    var flowIo = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        var flow = e.target;
        clearInterval(flow._timer);
        if (!e.isIntersecting) return;
        var steps = flow.querySelectorAll(".flow-step"), k = -1;
        flow._timer = setInterval(function () {
          if (k >= 0) steps[k].classList.remove("is-active");
          k = (k + 1) % steps.length;
          steps[k].classList.add("is-active");
        }, 1400);
      });
    }, { threshold: .4 });
    flows.forEach(function (f) { flowIo.observe(f); });
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

  // Interactive parts for the basics articles: {{< qubit >}}, {{< circuit >}} and {{< growth >}}.
  var SVG = "http://www.w3.org/2000/svg";
  function svgEl(tag, attrs, parent) {
    var e = document.createElementNS(SVG, tag);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function pct(p) {
    p = Math.round(p * 1e6) / 1e6;
    return (Math.round(p * 1000) / 10).toFixed(p > 0 && p < 1 ? 1 : 0) + "%";
  }

  // One qubit drawn as an arrow: straight up is |0⟩, straight down |1⟩; P(0) = cos²(θ/2).
  document.querySelectorAll(".qx-qubit").forEach(function (fig) {
    var range = fig.querySelector("input"), arrow = fig.querySelector(".qx-arrow"), tip = fig.querySelector(".qx-tip");
    var status = fig.querySelector(".qx-status"), counts = [0, 0], snap = 0;
    function draw(theta) {
      var rad = theta * Math.PI / 180, x = 110 + 84 * Math.sin(rad), y = 112 - 84 * Math.cos(rad);
      arrow.setAttribute("x2", x.toFixed(1)); arrow.setAttribute("y2", y.toFixed(1));
      tip.setAttribute("cx", x.toFixed(1)); tip.setAttribute("cy", y.toFixed(1));
    }
    function p0() { var t = range.value * Math.PI / 360; return Math.cos(t) * Math.cos(t); }
    function update() {
      var p = p0();
      fig.querySelector(".js-p0").textContent = pct(p);
      fig.querySelector(".js-p1").textContent = pct(1 - p);
      fig.querySelector(".js-b0").style.width = p * 100 + "%";
      fig.querySelector(".js-b1").style.width = (1 - p) * 100 + "%";
      clearTimeout(snap);
      fig.classList.remove("is-collapsed");
      draw(+range.value);
    }
    function tally(last) {
      status.textContent = (last === undefined ? "" : "結果は " + last + "。") +
        "これまで：0 が " + counts[0] + "回、1 が " + counts[1] + "回";
    }
    range.addEventListener("input", function () { counts = [0, 0]; update(); status.textContent = "矢印を傾けてから、測定してみよう"; });
    fig.querySelector(".js-measure").addEventListener("click", function () {
      var r = Math.random() < p0() ? 0 : 1;
      counts[r]++;
      tally(r);
      // Measuring leaves the qubit in the state it showed; the arrow jumps there, then the same state is prepared again.
      clearTimeout(snap);
      fig.classList.add("is-collapsed");
      draw(r ? 180 : 0);
      snap = setTimeout(function () { fig.classList.remove("is-collapsed"); draw(+range.value); }, reduceMotion ? 0 : 900);
    });
    fig.querySelector(".js-many").addEventListener("click", function () {
      var p = p0();
      for (var i = 0; i < 100; i++) counts[Math.random() < p ? 0 : 1]++;
      tally();
    });
    fig.querySelector(".js-reset").addEventListener("click", function () { counts = [0, 0]; tally(); });
    update();
  });

  // 2 to the n, exactly (BigInt) and in Japanese units, with a cell per pattern while they still fit.
  var UNITS = [["京", 16], ["兆", 12], ["億", 8], ["万", 4]];
  document.querySelectorAll(".qx-growth").forEach(function (fig) {
    var range = fig.querySelector("input"), cells = fig.querySelector(".qx-cells");
    function update() {
      var n = +range.value, count = window.BigInt ? BigInt(2) ** BigInt(n) : Math.pow(2, n);
      fig.querySelectorAll(".js-n").forEach(function (b) { b.textContent = n; });
      fig.querySelector(".js-count").textContent = count.toLocaleString("ja-JP") + " 個";
      var approx = "", digits = count.toString().length;
      for (var i = 0; i < UNITS.length; i++) {
        if (digits > UNITS[i][1]) { approx = "約" + count.toString().slice(0, digits - UNITS[i][1]) + UNITS[i][0]; break; }
      }
      fig.querySelector(".js-approx").textContent = approx;
      cells.textContent = "";
      cells.className = "qx-draw qx-cells" + (n <= 4 ? " is-labeled" : n <= 8 ? "" : " is-many");
      if (n > 8) {
        cells.textContent = "パターンが多すぎて、マス目では描けません";
        return;
      }
      var total = Math.pow(2, n);
      cells.style.setProperty("--cols", Math.pow(2, Math.ceil(n / 2)));
      for (var k = 0; k < total; k++) {
        var c = document.createElement("span");
        if (n <= 4) c.textContent = k.toString(2).padStart(n, "0");
        c.style.setProperty("--d", Math.min(k * 12, 600) + "ms");
        cells.appendChild(c);
      }
    }
    range.addEventListener("input", update);
    update();
  });

  // A state-vector simulator for 1-3 qubits and the real gates H, X, Z, CNOT; qubit 0 is the leftmost digit.
  var GATE_TEXT = {
    H: "H ゲート：量子ビット{0}を、0 と 1 が半々の重ね合わせにする（もう一度かけると元にもどる）",
    X: "X ゲート：量子ビット{0}の 0 と 1 を入れかえる（ふつうの NOT と同じ）",
    Z: "Z ゲート：量子ビット{0}が 1 の部分の符号を反転する（確率は変わらない）",
    CNOT: "CNOT ゲート：量子ビット{0}が 1 のときだけ、量子ビット{1}を反転する",
    M: "測定：確率にしたがって、どれか1つの結果が出る"
  };
  document.querySelectorAll(".qx-circuit").forEach(function (fig) {
    var n = +fig.dataset.qubits, gates = fig.dataset.gates.split(";").map(function (g) { return g.trim().split(/\s+/); });
    var dim = Math.pow(2, n), step = 0, counts = {};
    var wires = fig.querySelector(".js-wires"), statesBox = fig.querySelector(".js-states");
    var stepText = fig.querySelector(".qx-step"), status = fig.querySelector(".qx-status");
    var measureRow = fig.querySelector(".js-measure-row");
    function bit(i, q) { return (i >> (n - 1 - q)) & 1; }
    function label(i) { return "|" + i.toString(2).padStart(n, "0") + "⟩"; }
    function run(upto) {
      var a = new Array(dim).fill(0);
      a[0] = 1;
      for (var s = 0; s < upto; s++) {
        var g = gates[s], q = +g[1], b = new Array(dim).fill(0), i;
        if (g[0] === "H") {
          for (i = 0; i < dim; i++) {
            var j = i ^ (1 << (n - 1 - q)), sign = bit(i, q) ? -1 : 1;
            b[i] = (sign * a[i] + a[j]) / Math.SQRT2;
          }
        } else if (g[0] === "X") {
          for (i = 0; i < dim; i++) b[i ^ (1 << (n - 1 - q))] = a[i];
        } else if (g[0] === "Z") {
          for (i = 0; i < dim; i++) b[i] = bit(i, q) ? -a[i] : a[i];
        } else if (g[0] === "CNOT") {
          var t = +g[2];
          for (i = 0; i < dim; i++) b[bit(i, q) ? i ^ (1 << (n - 1 - t)) : i] = a[i];
        } else {
          b = a;
        }
        a = b;
      }
      return a.map(function (x) { return Math.abs(x) < 1e-12 ? 0 : x; });
    }
    // Draw the wires and gates once; columns light up as the steps are applied.
    var colW = 46, left = 52, w = left + colW * gates.length + 16, rowH = 42, h = 18 + rowH * n;
    wires.setAttribute("viewBox", "0 0 " + w + " " + h);
    var cols = [];
    for (var q = 0; q < n; q++) {
      var y = 30 + rowH * q;
      svgEl("line", { x1: 40, y1: y, x2: w - 8, y2: y, class: "qx-wire" }, wires);
      svgEl("text", { x: 4, y: y + 4, class: "qx-small" }, wires).textContent = "q" + q + " |0⟩";
    }
    gates.forEach(function (g, s) {
      var x = left + colW * s + colW / 2, col = svgEl("g", { class: "qx-col" }, wires);
      svgEl("rect", { x: x - colW / 2 + 2, y: 6, width: colW - 4, height: h - 12, rx: 8, class: "qx-band" }, col);
      function box(qq, text) {
        var yy = 30 + rowH * qq;
        svgEl("rect", { x: x - 14, y: yy - 14, width: 28, height: 28, rx: 6, class: "qx-gate" }, col);
        svgEl("text", { x: x, y: yy + 5, class: "qx-gate-text" }, col).textContent = text;
      }
      if (g[0] === "CNOT") {
        var yc = 30 + rowH * +g[1], yt = 30 + rowH * +g[2];
        svgEl("line", { x1: x, y1: yc, x2: x, y2: yt, class: "qx-link" }, col);
        svgEl("circle", { cx: x, cy: yc, r: 5, class: "qx-dot" }, col);
        svgEl("circle", { cx: x, cy: yt, r: 11, class: "qx-target" }, col);
        svgEl("path", { d: "M" + (x - 11) + " " + yt + "h22M" + x + " " + (yt - 11) + "v22", class: "qx-link" }, col);
      } else if (g[0] === "M") {
        for (var k = 0; k < n; k++) {
          box(k, "");
          var ym = 30 + rowH * k;
          svgEl("path", { d: "M" + (x - 8) + " " + (ym + 5) + "a8 8 0 0 1 16 0M" + x + " " + (ym + 5) + "l5-9", class: "qx-meter" }, col);
        }
      } else {
        box(+g[1], g[0]);
      }
      cols.push(col);
    });
    function show() {
      var amps = run(step), measured = step === gates.length && gates[gates.length - 1][0] === "M";
      cols.forEach(function (c, i) {
        c.classList.toggle("is-done", i < step);
        c.classList.toggle("is-current", i === step - 1);
      });
      var g = gates[step - 1];
      stepText.textContent = step === 0 ? "スタート：すべての量子ビットが |0⟩"
        : "ステップ" + step + "　" + GATE_TEXT[g[0]].replace("{0}", g[1]).replace("{1}", g[2]);
      statesBox.textContent = "";
      statesBox.classList.toggle("is-two-columns", dim > 4);
      var negative = false;
      amps.forEach(function (a, i) {
        var p = a * a, row = document.createElement("div");
        row.className = "qx-state" + (a < 0 ? " is-negative" : "") + (p === 0 ? " is-zero" : "");
        row.innerHTML = '<span class="qx-ket"></span><i><s></s></i><b></b>';
        row.querySelector(".qx-ket").textContent = (a < 0 ? "−" : "") + label(i);
        row.querySelector("s").style.width = p * 100 + "%";
        row.querySelector("b").textContent = pct(p);
        if (a < 0) negative = true;
        statesBox.appendChild(row);
      });
      if (negative) {
        var note = document.createElement("p");
        note.className = "qx-small-note";
        note.textContent = "「−」は符号がマイナスの部分。確率は同じでも、次のゲートで打ち消し合ったり強め合ったりします";
        statesBox.appendChild(note);
      }
      measureRow.hidden = !measured;
      if (!measured) { counts = {}; status.textContent = ""; }
      fig.querySelector(".js-back").disabled = step === 0;
      fig.querySelector(".js-next").disabled = step === gates.length;
    }
    function sample(amps) {
      var r = Math.random(), acc = 0;
      for (var i = 0; i < dim; i++) { acc += amps[i] * amps[i]; if (r < acc) return i; }
      return dim - 1;
    }
    function tally(last) {
      var parts = [];
      for (var i = 0; i < dim; i++) if (counts[i]) parts.push(label(i) + " " + counts[i] + "回");
      status.textContent = (last === undefined ? "" : "結果は " + label(last) + "。") + "これまで：" + parts.join("・");
    }
    fig.querySelector(".js-next").addEventListener("click", function () { if (step < gates.length) { step++; show(); } });
    fig.querySelector(".js-back").addEventListener("click", function () { if (step > 0) { step--; show(); } });
    fig.querySelector(".js-reset").addEventListener("click", function () { step = 0; show(); });
    fig.querySelector(".js-measure").addEventListener("click", function () {
      var r = sample(run(step));
      counts[r] = (counts[r] || 0) + 1;
      tally(r);
    });
    fig.querySelector(".js-many").addEventListener("click", function () {
      var amps = run(step);
      for (var i = 0; i < 100; i++) { var r = sample(amps); counts[r] = (counts[r] || 0) + 1; }
      tally();
    });
    show();
  });

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
