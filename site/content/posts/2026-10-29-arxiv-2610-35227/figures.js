// Figures for this article only (inlined with {{< htmlfig >}}). Each <figure data-hf="name"> is drawn by figs[name].
// Animations run only while a figure is on screen, and stay still for readers who ask for reduced motion.
(function () {
  "use strict";
  if (window.hfLoaded) return; // the script tag comes with every figure
  window.hfLoaded = true;

  var reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  var NS = "http://www.w3.org/2000/svg";

  function svg(tag, attrs, parent) {
    var e = document.createElementNS(NS, tag);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function label(text, attrs, parent) {
    var t = svg("text", attrs, parent);
    t.textContent = text;
    return t;
  }
  function $(root, sel) { return root.querySelector(sel); }
  function press(buttons, pick) {
    buttons.forEach(function (b) { b.setAttribute("aria-pressed", pick(b) ? "true" : "false"); });
  }
  // Path through points (x, y) for u = 0..1.
  function curve(x0, x1, steps, y) {
    var d = "";
    for (var i = 0; i <= steps; i++) {
      var u = i / steps;
      d += (i ? "L" : "M") + (x0 + (x1 - x0) * u).toFixed(1) + " " + y(u).toFixed(1);
    }
    return d;
  }
  // Calls draw(t) every frame while the figure is visible; returns redraw() for changes made while paused.
  function animate(root, draw) {
    var running = false, raf = 0, t = 0, last = 0;
    function frame(now) {
      if (last) t += Math.min(0.05, (now - last) / 1000);
      last = now;
      draw(t);
      if (running) raf = requestAnimationFrame(frame);
    }
    if (!reduce && "IntersectionObserver" in window) {
      new IntersectionObserver(function (entries) {
        var visible = entries[entries.length - 1].isIntersecting;
        if (visible && !running) { running = true; last = 0; raf = requestAnimationFrame(frame); }
        if (!visible && running) { running = false; cancelAnimationFrame(raf); }
      }).observe(root);
    }
    draw(t);
    return function () { if (!running) draw(t); };
  }

  var COLORS = ["var(--f1)", "var(--f2)", "var(--f3)", "var(--f4)"];
  var figs = {};

  figs.fridge = function (root) {
    var linesG = $(root, ".js-lines"), dotsG = $(root, ".js-dots"), meter = $(root, ".js-meter");
    var status = $(root, ".hf-status"), buttons = root.querySelectorAll("[data-mode]");
    var qx = [], dots = [], level = 0, target = 1;
    for (var k = 0; k < 8; k++) {
      qx.push(153.5 + k * 13.5);
      svg("circle", { cx: qx[k], cy: 257, r: 4.2, fill: "var(--accent-2)" }, $(root, ".js-qubits"));
    }
    function build(mode) {
      linesG.textContent = "";
      dotsG.textContent = "";
      dots = [];
      var long = [];
      if (mode === "dedicated") {
        for (var i = 0; i < 24; i++) long.push([106 + i * 180 / 23, 0, qx[Math.floor(i / 3)] + (i % 3 - 1) * 3, 246]);
      } else {
        long = [[184, 0, 194, 222], [208, 0, 206, 222]];
      }
      long.forEach(function (l) { svg("line", { x1: l[0], y1: l[1], x2: l[2], y2: l[3], class: "wire" }, linesG); });
      if (mode !== "dedicated") {
        qx.forEach(function (x) { svg("line", { x1: 200, y1: 236, x2: x, y2: 246, class: "wire" }, linesG); });
        svg("rect", { x: 168, y: 221, width: 64, height: 16, rx: 4, class: "box" }, linesG);
        label("振り分け", { x: 200, y: 233, "text-anchor": "middle", class: "t-small" }, linesG);
      }
      long.forEach(function (l, i) {
        for (var n = 0; n < 2; n++) dots.push({ l: l, o: (i * 0.37 + n * 0.5) % 1, el: svg("circle", { r: 2.6, fill: "var(--hot)" }, dotsG) });
      });
      target = mode === "dedicated" ? 1 : 0.17;
      status.textContent = mode === "dedicated"
        ? "線24本：熱がどんどん流れこみ、冷やしきれない"
        : "線2本（12分の1）：入る熱が減り、冷やせる量に収まる";
      press(buttons, function (b) { return b.dataset.mode === mode; });
    }
    var redraw = animate(root, function (t) {
      dots.forEach(function (d) {
        var u = (t * 0.35 + d.o) % 1;
        d.el.setAttribute("cx", d.l[0] + (d.l[2] - d.l[0]) * u);
        d.el.setAttribute("cy", d.l[1] + (d.l[3] - d.l[1]) * u);
      });
      level = reduce || t === 0 ? target : level + (target - level) * 0.06;
      var h = 166 * level;
      meter.setAttribute("y", 228 - h);
      meter.setAttribute("height", h);
      meter.setAttribute("fill", level > 0.4 ? "var(--hot)" : "var(--cool)");
    });
    buttons.forEach(function (b) { b.addEventListener("click", function () { build(b.dataset.mode); redraw(); }); });
    build("dedicated");
    redraw();
  };

  figs.pulse = function (root) {
    var pulse = $(root, ".js-pulse"), wave = $(root, ".js-wave"), box1 = $(root, ".js-box1"), box2 = $(root, ".js-box2");
    function flat(x, c, half) { // flat-top bump of half-width `half` around c
      var d = Math.abs(x - c) - (half - 14);
      return d <= 0 ? 1 : d >= 14 ? 0 : 0.5 + 0.5 * Math.cos(Math.PI * d / 14);
    }
    animate(root, function (t) {
      var c = -70 + ((t * 0.28) % 1) * 380 + (t === 0 ? 190 : 0);
      pulse.setAttribute("d", curve(6, 250, 160, function (u) { return 94 - 46 * flat(6 + 244 * u, c, 34); }));
      wave.setAttribute("d", curve(6, 250, 320, function (u) {
        var x = 6 + 244 * u;
        return 210 - 40 * flat(x, c, 52) * Math.sin(2 * Math.PI * (x / 12) - t * 9);
      }));
      var hit = c > 236 && c < 300;
      box1.setAttribute("stroke", hit ? "var(--hot)" : "var(--border)");
      box2.setAttribute("stroke", hit ? "var(--f2)" : "var(--border)");
      box1.setAttribute("stroke-width", hit ? 2.5 : 1.5);
      box2.setAttribute("stroke-width", hit ? 2.5 : 1.5);
    });
  };

  figs.fdm = function (root) {
    var stat = $(root, ".js-static"), wavesG = $(root, ".js-waves"), sum = $(root, ".js-sum");
    var status = $(root, ".hf-status"), toggles = $(root, ".js-toggles");
    var freqs = ["51MHz", "134MHz", "307MHz", "552MHz"], cycles = [2, 3, 5, 8];
    var on = [true, true, true, true], parts = [];
    for (var k = 0; k < 4; k++) {
      var x = 6 + k * 88, cx = x + 41;
      svg("rect", { x: x, y: 6, width: 82, height: 56, rx: 8, class: "box" }, stat);
      label(freqs[k], { x: cx, y: 56, "text-anchor": "middle", class: "t-small t-bold" }, stat);
      svg("path", { d: "M" + cx + " 62 C" + cx + " 80 180 74 180 92", class: "wire" }, stat);
      svg("path", { d: "M180 194 C180 208 " + cx + " 206 " + cx + " 222", class: "wire" }, stat);
      svg("rect", { x: x + 4, y: 222, width: 74, height: 40, rx: 8, fill: "var(--surface)", stroke: COLORS[k], "stroke-width": 2 }, stat);
      label("フィルター", { x: cx, y: 238, "text-anchor": "middle", class: "t-small" }, stat);
      label(freqs[k] + "だけ", { x: cx, y: 254, "text-anchor": "middle", class: "t-small t-muted" }, stat);
      svg("line", { x1: cx, y1: 318, x2: cx, y2: 340, class: "wire" }, stat);
      svg("circle", { cx: x + 18, cy: 352, r: 9, fill: "var(--surface)", stroke: "var(--muted)", "stroke-width": 1.4 }, stat);
      svg("circle", { cx: x + 64, cy: 352, r: 9, fill: "var(--surface)", stroke: "var(--muted)", "stroke-width": 1.4 }, stat);
      label("カプラー" + (k + 1), { x: cx, y: 386, "text-anchor": "middle", class: "t-small" }, stat);
      parts.push({
        x: x,
        src: svg("path", { fill: "none", stroke: COLORS[k], "stroke-width": 2 }, wavesG),
        out: svg("path", { fill: "none", stroke: COLORS[k], "stroke-width": 2 }, wavesG),
        coupler: svg("rect", { x: x + 29, y: 346, width: 24, height: 12, rx: 6 }, wavesG)
      });
      var b = document.createElement("button");
      b.type = "button";
      b.className = "hf-btn";
      b.style.setProperty("--c", COLORS[k]);
      b.innerHTML = '<span class="dot"></span>' + freqs[k];
      b.dataset.k = k;
      toggles.appendChild(b);
    }
    svg("rect", { x: 6, y: 92, width: 348, height: 58, rx: 10, class: "box" }, stat);
    label("1本の線", { x: 14, y: 110, class: "t-small t-bold" }, stat);
    svg("line", { x1: 180, y1: 150, x2: 180, y2: 172, class: "wire" }, stat);
    svg("rect", { x: 140, y: 172, width: 80, height: 22, rx: 6, class: "box" }, stat);
    label("分配器", { x: 180, y: 188, "text-anchor": "middle", class: "t-small" }, stat);
    var buttons = toggles.querySelectorAll("button");

    var redraw = animate(root, function (t) {
      var n = on.filter(Boolean).length;
      parts.forEach(function (p, k) {
        var amp = on[k] ? 1 : 0;
        p.src.setAttribute("d", curve(p.x + 6, p.x + 76, 140, function (u) {
          return 28 - 11 * amp * Math.sin(2 * Math.PI * cycles[k] * u - t * 4);
        }));
        p.src.setAttribute("opacity", on[k] ? 1 : 0.35);
        p.out.setAttribute("d", curve(p.x + 6, p.x + 76, 140, function (u) {
          return 293 - 12 * amp * Math.sin(2 * Math.PI * cycles[k] * u - t * 4);
        }));
        p.out.setAttribute("stroke", on[k] ? COLORS[k] : "var(--line)");
        p.coupler.setAttribute("fill", on[k] ? COLORS[k] : "var(--soft)");
        p.coupler.setAttribute("stroke", on[k] ? "none" : "var(--border)");
        p.coupler.setAttribute("opacity", on[k] ? 0.65 + 0.35 * Math.sin(t * 5 + k) : 1);
      });
      sum.setAttribute("d", curve(70, 346, 400, function (u) {
        var s = 0;
        for (var k = 0; k < 4; k++) if (on[k]) s += Math.sin(2 * Math.PI * cycles[k] * u * 3.5 - t * 4 + k);
        return 124 - 18 * s / Math.max(1, Math.sqrt(n) * 1.3);
      }));
    });
    function update() {
      var n = on.filter(Boolean).length;
      press(buttons, function (b) { return on[b.dataset.k]; });
      status.textContent = n === 0 ? "命令なし：どのカプラーも動きません"
        : n + "つのゲートを1本の線で同時に動かしています（混ざらずにそれぞれの宛先へ）";
      redraw();
    }
    buttons.forEach(function (b) { b.addEventListener("click", function () { on[b.dataset.k] = !on[b.dataset.k]; update(); }); });
    update();
  };

  figs.groups = function (root) {
    var grid = $(root, ".js-grid"), legend = $(root, ".js-legend"), status = $(root, ".hf-status");
    var names = ["A", "B", "C", "D"], couplers = [], boxes = [], pos = function (i) { return 40 + i * 70; };
    for (var j = 0; j < 4; j++) {
      for (var i = 0; i < 4; i++) {
        if (i < 3) couplers.push({ g: i === 1 ? 1 : 0, el: svg("rect", { x: pos(i) + 13, y: pos(j) - 5, width: 44, height: 10, rx: 5 }, grid) });
        if (j < 3) couplers.push({ g: j === 1 ? 3 : 2, el: svg("rect", { x: pos(i) - 5, y: pos(j) + 13, width: 10, height: 44, rx: 5 }, grid) });
      }
    }
    for (j = 0; j < 4; j++) for (i = 0; i < 4; i++) svg("circle", { cx: pos(i), cy: pos(j), r: 11, fill: "var(--surface)", stroke: "var(--muted)", "stroke-width": 1.4 }, grid);
    var count = [0, 0, 0, 0];
    couplers.forEach(function (c) { c.color = COLORS[count[c.g]++ % 4]; });
    names.forEach(function (n, k) {
      var y = 26 + k * 62;
      boxes.push(svg("rect", { x: 290, y: y, width: 64, height: 46, rx: 10 }, legend));
      label("時刻" + (k + 1), { x: 322, y: y + 19, "text-anchor": "middle", class: "t-small t-muted" }, legend);
      label("グループ" + n, { x: 322, y: y + 36, "text-anchor": "middle", class: "t-small t-bold" }, legend);
    });
    var buttonsBox = $(root, ".js-buttons"), playing = !reduce, current = 0, offset = 0;
    var play = document.createElement("button");
    play.type = "button";
    play.className = "hf-btn";
    buttonsBox.appendChild(play);
    names.forEach(function (n, k) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "hf-btn";
      b.textContent = n;
      b.setAttribute("aria-label", "グループ" + n);
      b.dataset.g = k;
      buttonsBox.appendChild(b);
    });
    var groupButtons = buttonsBox.querySelectorAll("[data-g]");
    var lastT = 0;
    function show(g, t) {
      couplers.forEach(function (c) {
        var active = c.g === g;
        c.el.setAttribute("fill", active ? c.color : "var(--border)");
        c.el.setAttribute("opacity", active ? 0.7 + 0.3 * Math.sin(t * 5) : 0.6);
      });
      boxes.forEach(function (b, k) {
        b.setAttribute("fill", k === g ? "var(--soft)" : "var(--surface)");
        b.setAttribute("stroke", k === g ? "var(--accent)" : "var(--border)");
        b.setAttribute("stroke-width", k === g ? 2.2 : 1.2);
      });
      if (g !== current || !status.textContent) {
        current = g;
        status.textContent = "時刻" + (g + 1) + "：グループ" + names[g] + "のカプラー" + count[g] + "個が、色（周波数）で分かれて同時に動く";
        press(groupButtons, function (b) { return +b.dataset.g === g; });
      }
    }
    var redraw = animate(root, function (t) {
      lastT = t;
      show(playing ? Math.floor((t - offset) / 1.8) % 4 : current, t);
    });
    function setPlay(p) {
      playing = p;
      play.textContent = p ? "❚❚ 止める" : "▶ 自動";
      play.setAttribute("aria-pressed", p ? "true" : "false");
    }
    play.addEventListener("click", function () {
      if (!playing) offset = lastT - current * 1.8;
      setPlay(!playing);
      redraw();
    });
    groupButtons.forEach(function (b) {
      b.addEventListener("click", function () { setPlay(false); show(+b.dataset.g, lastT); redraw(); });
    });
    setPlay(playing);
    show(0, 0);
  };

  figs.spectrum = function (root) {
    var curves = $(root, ".js-curves"), leak = $(root, ".js-leak"), band = $(root, ".js-band");
    var range = $(root, "input"), status = $(root, ".hf-status"), lenButtons = root.querySelectorAll("[data-len]");
    var width = 14, half = 14, c = 180, base = 170, height = 118;
    var paths = [0, 1, 2].map(function (k) {
      return svg("path", { fill: COLORS[k], "fill-opacity": 0.14, stroke: COLORS[k], "stroke-width": 1.8 }, curves);
    });
    band.setAttribute("x", c - half);
    band.setAttribute("width", half * 2);
    function spec(x, center) {
      var u = (x - center) / width;
      return Math.exp(-u * u / 2);
    }
    function draw() {
      var s = +range.value, centers = [c - s, c, c + s];
      paths.forEach(function (p, k) {
        p.setAttribute("d", curve(8, 352, 344, function (u) { return base - height * spec(8 + 344 * u, centers[k]); }) + "L352 " + base + "L8 " + base + "Z");
      });
      var d = "M" + (c - half) + " " + base, inside = 0, wanted = 0;
      for (var x = c - half; x <= c + half; x += 0.5) {
        var n = Math.max(spec(x, centers[0]), spec(x, centers[2]));
        inside += spec(x, centers[0]) + spec(x, centers[2]);
        wanted += spec(x, c);
        d += "L" + x + " " + (base - height * n).toFixed(1);
      }
      leak.setAttribute("d", d + "L" + (c + half) + " " + base + "Z");
      var r = inside / wanted;
      status.textContent = r < 0.01 ? "混信：ほぼなし" : r < 0.08 ? "混信：少しある" : "混信：大きい（ゲートの誤りの原因に）";
      status.style.color = r < 0.01 ? "var(--accent)" : r < 0.08 ? "var(--fg)" : "var(--hot)";
    }
    range.addEventListener("input", draw);
    lenButtons.forEach(function (b) {
      b.addEventListener("click", function () {
        width = b.dataset.len === "long" ? 6 : 14;
        press(lenButtons, function (x) { return x === b; });
        draw();
      });
    });
    draw();
  };

  figs.conditions = function (root) {
    var chain = $(root, ".js-chain"), runner = $(root, ".js-runner"), verdict = $(root, ".hf-verdict");
    var send = $(root, ".js-send"), recv = $(root, ".js-recv"), tabs = root.querySelectorAll("[data-cond]");
    var names = ["制御装置", "補正", "分配器", "フィルター", "チップ"], from = [1, 3, 4, 2, 1], boxes = [];
    names.forEach(function (n, k) {
      var x = 4 + k * 71;
      if (k) svg("line", { x1: x - 9, y1: 34, x2: x, y2: 34, class: "wire" }, chain);
      var g = svg("g", {}, chain);
      svg("rect", { x: x, y: 18, width: 62, height: 32, rx: 8, class: "box" }, g);
      label(n, { x: x + 31, y: 38, "text-anchor": "middle", class: "t-small" }, g);
      boxes.push(g);
    });
    // Waveforms on 0..1: the pulse we want, what a low-pass filter makes of it, and the pre-distorted input that undoes the filter.
    var N = 200, tau = 0.04, ideal = [], filtered = [], pre = [];
    for (var i = 0; i <= N; i++) {
      var u = i / N, e = u < 0.15 || u > 0.85 ? 0 : u < 0.3 ? 0.5 - 0.5 * Math.cos(Math.PI * (u - 0.15) / 0.15)
        : u > 0.7 ? 0.5 + 0.5 * Math.cos(Math.PI * (u - 0.7) / 0.15) : 1;
      ideal.push(e);
    }
    for (i = 0; i <= N; i++) pre.push(ideal[i] + tau * N * (ideal[Math.min(N, i + 1)] - ideal[Math.max(0, i - 1)]) / 2);
    function lowpass(x) {
      var y = [], v = 0, a = 1 / (N * tau);
      x.forEach(function (s) { v += (s - v) * a; y.push(v); });
      return y;
    }
    filtered = lowpass(ideal);
    var restored = lowpass(pre);
    function plot(path, x0, data) {
      path.setAttribute("d", curve(x0 + 8, x0 + 158, N, function (u) { return 206 - 64 * data[Math.round(u * N)]; }));
    }
    plot($(root, ".js-ideal-a"), 8, ideal);
    plot($(root, ".js-ideal-b"), 186, ideal);
    var text = {
      1: "<b>基準：</b>1つの対象に1つの専用チャンネル。送った波形がそのまま届きます。",
      2: "<b>誤りが増えた：</b>フィルターを通ると波形がなまってしまい、1量子ビットゲートでも2量子ビットゲートでも誤りが増えました。",
      3: "<b>もとにもどった：</b>ゆがみを先回りして打ち消す波形を送ると、届く波形は本来の形に。誤りは専用線と区別できない水準になりました。",
      4: "<b>増えなかった：</b>分配器を足して1本の線から4つを同時に動かしても、誤りに統計的に意味のある増加はありませんでした。"
    };
    var cond = 1;
    function select(c) {
      cond = c;
      press(tabs, function (b) { return +b.dataset.cond === c; });
      boxes.forEach(function (g, k) {
        var used = c >= from[k];
        g.setAttribute("opacity", used ? 1 : 0.28);
        g.firstChild.setAttribute("stroke-dasharray", used ? "none" : "3 3");
      });
      plot(send, 8, c >= 3 ? pre : ideal);
      plot(recv, 186, c === 1 ? ideal : c === 2 ? filtered : restored);
      recv.setAttribute("stroke", c === 2 ? "var(--hot)" : "var(--accent)");
      verdict.innerHTML = text[c];
      verdict.classList.toggle("is-bad", c === 2);
      [send, recv].forEach(function (p) {
        if (reduce || !p.getTotalLength) return;
        var len = p.getTotalLength();
        p.style.transition = "none";
        p.style.strokeDasharray = len;
        p.style.strokeDashoffset = len;
        p.getBoundingClientRect();
        p.style.transition = "stroke-dashoffset .9s ease-out";
        p.style.strokeDashoffset = 0;
      });
    }
    animate(root, function (t) {
      runner.setAttribute("cx", 35 + ((t * 0.45) % 1) * 284);
      runner.setAttribute("opacity", reduce ? 0 : 1);
    });
    tabs.forEach(function (b) { b.addEventListener("click", function () { select(+b.dataset.cond); }); });
    select(cond);
  };

  figs.heat = function (root) {
    var range = $(root, "input"), marker = $(root, ".js-marker"), status = $(root, ".hf-status");
    var buttons = root.querySelectorAll("[data-r]"), ticks = $(root, ".js-ticks");
    function xOf(v) { return 30 + Math.log10(v) / 3 * 310; }
    var band = $(root, ".js-band");
    band.setAttribute("x", xOf(5));
    band.setAttribute("width", xOf(30) - xOf(5));
    [1, 10, 100, 1000].forEach(function (v) {
      svg("line", { x1: xOf(v), y1: 70, x2: xOf(v), y2: 76, stroke: "var(--muted)" }, ticks);
      label(v + "μW", { x: xOf(v), y: 92, "text-anchor": "middle", class: "t-small t-muted" }, ticks);
    });
    // Line counts the paper gives for its two cases; other ratios divide the 1,143,000 dedicated lines.
    var paperLines = { 1: 1143000, 12: 95250, 48: 23814 };
    function update() {
      var r = +range.value, heat = 400.05 / r, ok = heat <= 30;
      var lines = paperLines[r] || Math.round(1143000 / r);
      $(root, ".js-r").textContent = r;
      $(root, ".js-lines").textContent = (paperLines[r] ? "" : "約") + lines.toLocaleString("ja-JP") + "本";
      $(root, ".js-heat").textContent = heat.toFixed(2) + "μW";
      marker.style.transform = "translate(" + xOf(heat).toFixed(1) + "px, 0)";
      marker.setAttribute("fill", ok ? "var(--accent)" : "var(--hot)");
      marker.setAttribute("stroke", ok ? "var(--accent)" : "var(--hot)");
      status.textContent = ok ? "冷やせる範囲（5〜30μW）に収まる" : "冷やせる量（5〜30μW）を超える";
      status.style.color = ok ? "var(--accent)" : "var(--hot)";
      press(buttons, function (b) { return +b.dataset.r === r; });
    }
    range.addEventListener("input", update);
    buttons.forEach(function (b) { b.addEventListener("click", function () { range.value = b.dataset.r; update(); }); });
    update();
  };

  var roots = document.querySelectorAll("[data-hf]");
  roots.forEach(function (root) {
    var draw = figs[root.dataset.hf];
    if (draw) draw(root);
  });

  // Shrinks each drawing so that it, the buttons and the result line fit on screen together below
  // the sticky header. When the buttons sit beside the drawing (a wide figure), only the taller of
  // the two has to fit; when they sit under it (a phone), the drawing gets what the buttons leave.
  function height(el) {
    var s = getComputedStyle(el);
    return el.getBoundingClientRect().height + parseFloat(s.marginTop) + parseFloat(s.marginBottom);
  }
  function fit() {
    var header = document.querySelector(".site-header");
    var room = innerHeight - (header ? header.getBoundingClientRect().height : 0) - 28;
    roots.forEach(function (root) {
      var drawing = $(root, "svg"), side = $(root, ".hf-side");
      if (!drawing) return;
      drawing.style.maxHeight = "";
      var controls = 0;
      if (side && side.getBoundingClientRect().top < drawing.getBoundingClientRect().bottom - 1) {
        controls = 0; // beside the drawing
      } else if (side) {
        controls = height(side);
      } else {
        [].forEach.call(root.children, function (el) {
          if (el !== drawing && !el.matches(".hf-title, .hf-lead, .hf-note")) controls += height(el);
        });
      }
      drawing.style.maxHeight = Math.max(160, room - controls) + "px";
    });
  }
  var pending = 0;
  addEventListener("resize", function () { cancelAnimationFrame(pending); pending = requestAnimationFrame(fit); });
  fit();

  if ("IntersectionObserver" in window) {
    var inView = new Set();
    var seen = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) { if (e.isIntersecting) inView.add(e.target); else inView.delete(e.target); });
      document.body.classList.toggle("hf-in-view", inView.size > 0);
    });
    roots.forEach(function (root) { seen.observe(root); });
  }
})();
