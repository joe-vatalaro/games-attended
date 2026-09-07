(function () {
  const tablist = document.querySelector("[data-report-tabs]");
  if (!tablist) return;

  const buttons = Array.prototype.slice.call(tablist.querySelectorAll("[role='tab']"));
  const panels = Array.prototype.slice.call(document.querySelectorAll("[data-report-panel]"));
  const allowed = {};
  buttons.forEach(function (button) {
    allowed[button.getAttribute("data-tab")] = true;
  });

  function selectedButtonTab() {
    const selected = buttons.find(function (button) {
      return button.getAttribute("aria-selected") === "true";
    });
    return selected ? selected.getAttribute("data-tab") : null;
  }

  function tabFromLocation() {
    const params = new URLSearchParams(window.location.search);
    const fromQuery = params.get("tab");
    const fromHash = window.location.hash.replace(/^#/, "");
    if (allowed[fromQuery]) return fromQuery;
    if (allowed[fromHash]) return fromHash;
    return defaultTab();
  }

  function defaultTab() {
    const fallback = tablist.getAttribute("data-default-tab") || (buttons[0] && buttons[0].getAttribute("data-tab"));
    return allowed[fallback] ? fallback : (buttons[0] && buttons[0].getAttribute("data-tab"));
  }

  function show(tab, persist) {
    if (!allowed[tab]) tab = defaultTab();
    buttons.forEach(function (button) {
      const on = button.getAttribute("data-tab") === tab;
      button.setAttribute("aria-selected", on ? "true" : "false");
      button.tabIndex = on ? 0 : -1;
    });
    panels.forEach(function (panel) {
      const on = panel.getAttribute("data-report-panel") === tab;
      panel.hidden = !on;
    });
    if (!persist) return;
    const url = new URL(window.location.href);
    url.searchParams.set("tab", tab);
    url.hash = "";
    window.history.replaceState(null, "", url.pathname + url.search);
  }

  buttons.forEach(function (button) {
    button.addEventListener("click", function () {
      show(button.getAttribute("data-tab"), true);
    });
  });

  tablist.addEventListener("keydown", function (event) {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight" && event.key !== "Home" && event.key !== "End") {
      return;
    }
    event.preventDefault();
    const current = buttons.findIndex(function (button) {
      return button.getAttribute("aria-selected") === "true";
    });
    let next = current;
    if (event.key === "ArrowRight") next = (current + 1) % buttons.length;
    if (event.key === "ArrowLeft") next = (current - 1 + buttons.length) % buttons.length;
    if (event.key === "Home") next = 0;
    if (event.key === "End") next = buttons.length - 1;
    buttons[next].focus();
    show(buttons[next].getAttribute("data-tab"), true);
  });

  const filter = document.querySelector(".game-type-filter");
  if (filter) {
    filter.addEventListener("submit", function () {
      let input = filter.querySelector("input[name='tab']");
      if (!input) {
        input = document.createElement("input");
        input.type = "hidden";
        input.name = "tab";
        filter.appendChild(input);
      }
      input.value = selectedButtonTab() || tabFromLocation();
    });
  }

  const TABLE_PREVIEW_ROWS = 10;

  show(tabFromLocation(), false);
  initCollapsibleTables();
  initSprayChart();
  initExtremeCharts();

  function initCollapsibleTables() {
  document.querySelectorAll(".report-panel table").forEach(function (table) {
    if (table.classList.contains("extreme-table") || !table.tHead) return;
    const body = table.tBodies[0];
    if (!body || body.rows.length <= TABLE_PREVIEW_ROWS) return;
    const count = body.rows.length;
    table.classList.add("table-collapse", "is-collapsed");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "table-toggle";
    function sync() {
      const collapsed = table.classList.contains("is-collapsed");
      button.textContent = collapsed ? "Show all " + count : "Show fewer";
      button.setAttribute("aria-expanded", collapsed ? "false" : "true");
    }
    sync();
    button.addEventListener("click", function () {
      table.classList.toggle("is-collapsed");
      sync();
    });
    const host = table.closest(".table-scroll") || table;
    host.after(button);
  });
}

function initSprayChart() {
  const wrap = document.querySelector(".spray-chart-wrap");
  if (!wrap) return;
  const tooltip = wrap.querySelector(".spray-tooltip");
  const batterEl = tooltip.querySelector(".spray-tooltip-batter");
  const statsEl = tooltip.querySelector(".spray-tooltip-stats");
  const noteEl = tooltip.querySelector(".spray-tooltip-note");
  const dateEl = tooltip.querySelector(".spray-tooltip-date");
  const gameEl = tooltip.querySelector(".spray-tooltip-game");
  const hits = wrap.querySelectorAll(".spray-hit");
  let hideTimer = null;
  let active = null;
  let pinned = null;

  function clearHide() {
    if (hideTimer) {
      window.clearTimeout(hideTimer);
      hideTimer = null;
    }
  }

  function hide() {
    if (pinned) return;
    tooltip.hidden = true;
    tooltip.classList.remove("is-pinned");
    if (active) active.classList.remove("is-active");
    active = null;
  }

  function unpin() {
    pinned = null;
    hide();
  }

  function scheduleHide() {
    if (pinned) return;
    clearHide();
    hideTimer = window.setTimeout(hide, 160);
  }

  function fill(hit) {
    const batter = hit.getAttribute("data-batter") || "Unknown";
    const batterHref = hit.getAttribute("data-batter-href");
    batterEl.textContent = batter;
    if (batterHref) {
      batterEl.href = batterHref;
      batterEl.removeAttribute("aria-disabled");
    } else {
      batterEl.removeAttribute("href");
    }
    const bits = [];
    if (hit.getAttribute("data-distance")) bits.push(hit.getAttribute("data-distance") + " ft");
    if (hit.getAttribute("data-exit-velo")) bits.push(hit.getAttribute("data-exit-velo") + " mph");
    if (hit.getAttribute("data-launch-angle")) bits.push(hit.getAttribute("data-launch-angle") + "°");
    statsEl.textContent = bits.join(" · ");
    statsEl.hidden = bits.length === 0;
    noteEl.hidden = !hit.getAttribute("data-walkoff");
    dateEl.textContent = hit.getAttribute("data-date") || "";
    dateEl.hidden = !dateEl.textContent;
    const game = hit.getAttribute("data-game") || "";
    const gameHref = hit.getAttribute("data-game-href");
    gameEl.textContent = game;
    if (gameHref) {
      gameEl.href = gameHref;
    } else {
      gameEl.removeAttribute("href");
    }
    gameEl.hidden = !game;
  }

  function place(hit) {
    const wrapBox = wrap.getBoundingClientRect();
    const hitBox = hit.getBoundingClientRect();
    tooltip.hidden = false;
    const tipWidth = tooltip.offsetWidth;
    const tipHeight = tooltip.offsetHeight;
    let left = hitBox.left - wrapBox.left + hitBox.width / 2 - tipWidth / 2;
    let top = hitBox.top - wrapBox.top - tipHeight - 10;
    left = Math.max(8, Math.min(left, wrapBox.width - tipWidth - 8));
    if (top < 8) {
      top = hitBox.bottom - wrapBox.top + 10;
    }
    tooltip.style.left = left + "px";
    tooltip.style.top = top + "px";
  }

  function show(hit) {
    clearHide();
    if (active && active !== hit) active.classList.remove("is-active");
    active = hit;
    hit.classList.add("is-active");
    tooltip.classList.toggle("is-pinned", pinned === hit);
    fill(hit);
    place(hit);
  }

  function togglePin(hit) {
    if (pinned) {
      unpin();
      return;
    }
    pinned = hit;
    show(hit);
  }

  hits.forEach(function (hit) {
    hit.addEventListener("mouseenter", function () {
      if (pinned) return;
      show(hit);
    });
    hit.addEventListener("mouseleave", scheduleHide);
    hit.addEventListener("focus", function () {
      if (pinned) return;
      show(hit);
    });
    hit.addEventListener("blur", scheduleHide);
    hit.addEventListener("click", function (event) {
      event.preventDefault();
      event.stopPropagation();
      togglePin(hit);
    });
  });
  wrap.querySelector(".spray-chart").addEventListener("click", function () {
    if (pinned) unpin();
  });
  tooltip.addEventListener("mouseenter", clearHide);
  tooltip.addEventListener("mouseleave", scheduleHide);
  tooltip.addEventListener("click", function (event) {
    event.stopPropagation();
  });
}

function initExtremeCharts() {
  const dialog = document.querySelector("[data-extreme-dialog]");
  const dataEl = document.getElementById("extreme-chart-data");
  if (!dialog || !dataEl) return;

  const charts = JSON.parse(dataEl.textContent || "{}");
  const titleEl = dialog.querySelector("[data-extreme-title]");
  const summaryEl = dialog.querySelector("[data-extreme-summary]");
  const freqSvg = dialog.querySelector("[data-extreme-freq]");
  const timeSvg = dialog.querySelector("[data-extreme-time]");
  const wrap = dialog.querySelector(".extreme-chart-wrap");
  const tooltip = dialog.querySelector(".extreme-tooltip");
  const scoreEl = tooltip.querySelector(".extreme-tooltip-score");
  const valueEl = tooltip.querySelector(".extreme-tooltip-value");
  const dateEl = tooltip.querySelector(".extreme-tooltip-date");
  const venueEl = tooltip.querySelector(".extreme-tooltip-venue");
  const closeBtn = dialog.querySelector("[data-extreme-close]");
  const SVG = "http://www.w3.org/2000/svg";
  let hideTimer = null;

  document.querySelectorAll("[data-extreme]").forEach(function (button) {
    button.addEventListener("click", function () {
      openChart(button.getAttribute("data-extreme"), button.getAttribute("data-highlight"));
    });
  });
  closeBtn.addEventListener("click", closeChart);
  dialog.addEventListener("close", function () {
    persistExtreme(null, null);
  });
  tooltip.addEventListener("mouseenter", clearHideTip);
  tooltip.addEventListener("mouseleave", scheduleHideTip);

  const params = new URLSearchParams(window.location.search);
  const fromQuery = params.get("extreme");
  if (fromQuery && charts[fromQuery]) {
    openChart(fromQuery, params.get("hl") || "max");
  }

  function openChart(key, highlight) {
    const chart = charts[key];
    if (!chart || !chart.points || !chart.points.length) return;
    const marked = markedPoints(chart.points, highlight);
    titleEl.textContent = chart.title;
    summaryEl.textContent = summaryText(chart, marked, highlight);
    drawFrequency(freqSvg, chart, marked);
    drawTime(timeSvg, chart, marked);
    dialog.showModal();
    persistExtreme(key, highlight);
  }

  function closeChart() {
    dialog.close();
  }

  function persistExtreme(key, highlight) {
    const url = new URL(window.location.href);
    if (key) {
      url.searchParams.set("extreme", key);
      if (highlight) url.searchParams.set("hl", highlight);
    } else {
      url.searchParams.delete("extreme");
      url.searchParams.delete("hl");
    }
    window.history.replaceState(null, "", url.pathname + url.search);
  }

  function markedPoints(points, highlight) {
    if (highlight === "shutout") {
      return points.filter(function (point) {
        return point.shutout;
      });
    }
    if (!points.length) return [];
    const values = points.map(function (point) {
      return point.value;
    });
    const target = highlight === "min" ? Math.min.apply(null, values) : Math.max.apply(null, values);
    return points.filter(function (point) {
      return point.value === target;
    });
  }

  function markedIds(marked) {
    const ids = {};
    marked.forEach(function (point) {
      ids[point.id] = true;
    });
    return ids;
  }

  function summaryText(chart, marked, highlight) {
    const points = chart.points;
    const values = points.map(function (point) {
      return point.value;
    });
    const range = formatValue(Math.min.apply(null, values), chart.unit) + " – " + formatValue(Math.max.apply(null, values), chart.unit);
    if (highlight === "shutout") {
      return points.length + " games · " + marked.length + " shutouts";
    }
    return points.length + " games · " + range;
  }

  function formatValue(value, unit) {
    if (unit === "fans") return Number(value).toLocaleString() + " fans";
    if (unit === "°F") return value + "°F";
    if (unit === "min") return value + " min";
    if (unit === "inn") return value + " inn";
    return value + " " + unit;
  }

  function svgEl(name, attrs) {
    const el = document.createElementNS(SVG, name);
    Object.keys(attrs || {}).forEach(function (key) {
      el.setAttribute(key, attrs[key]);
    });
    return el;
  }

  function clearSvg(svg) {
    while (svg.firstChild) svg.removeChild(svg.firstChild);
  }

  function binsFor(points) {
    const values = points.map(function (point) {
      return point.value;
    });
    const unique = Array.from(new Set(values)).sort(function (a, b) {
      return a - b;
    });
    if (unique.length <= 12) {
      return unique.map(function (value) {
        return {
          start: value,
          end: value,
          label: String(value),
          count: values.filter(function (item) {
            return item === value;
          }).length,
          values: [value],
        };
      });
    }
    const min = unique[0];
    const max = unique[unique.length - 1];
    const count = 10;
    const width = (max - min) / count || 1;
    const bins = [];
    for (let i = 0; i < count; i += 1) {
      const start = min + width * i;
      const end = i === count - 1 ? max : min + width * (i + 1);
      bins.push({ start: start, end: end, label: "", count: 0, values: [] });
    }
    values.forEach(function (value) {
      let index = Math.floor((value - min) / width);
      if (index >= count) index = count - 1;
      if (index < 0) index = 0;
      bins[index].count += 1;
      bins[index].values.push(value);
    });
    bins.forEach(function (bin) {
      bin.label = compactNumber(bin.start) + "–" + compactNumber(bin.end);
    });
    return bins;
  }

  function compactNumber(value) {
    if (Math.abs(value) >= 1000) return Math.round(value / 100) / 10 + "k";
    return String(Math.round(value));
  }

  function binIsMarked(bin, marked) {
    return marked.some(function (point) {
      if (bin.start === bin.end) return point.value === bin.start;
      return point.value >= bin.start && point.value <= bin.end;
    });
  }

  function drawFrequency(svg, chart, marked) {
    clearSvg(svg);
    const bins = binsFor(chart.points);
    const width = 720;
    const height = 200;
    const left = 42;
    const right = 16;
    const top = 16;
    const bottom = 42;
    const innerW = width - left - right;
    const innerH = height - top - bottom;
    const maxCount = Math.max.apply(
      null,
      bins.map(function (bin) {
        return bin.count;
      })
    );
    const gap = bins.length > 8 ? 4 : 8;
    const barW = Math.max(8, innerW / bins.length - gap);
    bins.forEach(function (bin, index) {
      const barH = maxCount ? (bin.count / maxCount) * innerH : 0;
      const x = left + (innerW / bins.length) * index + gap / 2;
      const y = top + innerH - barH;
      if (barH) {
        svg.appendChild(
          svgEl("rect", {
            class: "extreme-bar" + (binIsMarked(bin, marked) ? " is-marked" : ""),
            x: x,
            y: y,
            width: barW,
            height: barH,
          })
        );
        svg.appendChild(
          svgEl("text", {
            class: "extreme-axis",
            x: x + barW / 2,
            y: y - 6,
            "text-anchor": "middle",
          })
        ).textContent = String(bin.count);
      }
      svg.appendChild(
        svgEl("text", {
          class: "extreme-axis",
          x: x + barW / 2,
          y: height - 18,
          "text-anchor": "middle",
        })
      ).textContent = bin.label;
    });
  }

  function drawTime(svg, chart, marked) {
    clearSvg(svg);
    const points = chart.points.slice();
    const width = 720;
    const height = 280;
    const left = 48;
    const right = 18;
    const top = 18;
    const bottom = 36;
    const innerW = width - left - right;
    const innerH = height - top - bottom;
    const times = points.map(function (point) {
      return dateTime(point.date);
    });
    const values = points.map(function (point) {
      return point.value;
    });
    let minT = Math.min.apply(null, times);
    let maxT = Math.max.apply(null, times);
    if (minT === maxT) {
      minT -= 86400000;
      maxT += 86400000;
    }
    let minV = Math.min.apply(null, values);
    let maxV = Math.max.apply(null, values);
    if (minV === maxV) {
      minV -= 1;
      maxV += 1;
    }
    const pad = (maxV - minV) * 0.08;
    minV -= pad;
    maxV += pad;
    const xAt = function (time) {
      return left + ((time - minT) / (maxT - minT)) * innerW;
    };
    const yAt = function (value) {
      return top + innerH - ((value - minV) / (maxV - minV)) * innerH;
    };
    svg.appendChild(svgEl("line", { class: "extreme-grid", x1: left, y1: top + innerH, x2: left + innerW, y2: top + innerH }));
    svg.appendChild(svgEl("line", { class: "extreme-grid", x1: left, y1: top, x2: left, y2: top + innerH }));
    yearTicks(minT, maxT).forEach(function (time) {
      const x = xAt(time);
      svg.appendChild(svgEl("line", { class: "extreme-grid faint", x1: x, y1: top, x2: x, y2: top + innerH }));
      svg.appendChild(
        svgEl("text", { class: "extreme-axis", x: x, y: height - 12, "text-anchor": "middle" })
      ).textContent = String(new Date(time).getUTCFullYear());
    });
    valueTicks(minV, maxV, chart.unit).forEach(function (value) {
      const y = yAt(value);
      svg.appendChild(svgEl("line", { class: "extreme-grid faint", x1: left, y1: y, x2: left + innerW, y2: y }));
      svg.appendChild(
        svgEl("text", { class: "extreme-axis", x: left - 8, y: y + 3, "text-anchor": "end" })
      ).textContent = axisValue(value, chart.unit);
    });
    const ids = markedIds(marked);
    points.forEach(function (point) {
      const markedDot = !!ids[point.id];
      const group = svgEl("g", {
        class: "extreme-hit" + (markedDot ? " is-marked" : ""),
        tabindex: "0",
        transform: "translate(" + (xAt(dateTime(point.date)) + jitter(point.id)) + " " + yAt(point.value) + ")",
      });
      group.appendChild(svgEl("circle", { class: "extreme-hit-target", r: "9" }));
      group.appendChild(svgEl("circle", { class: "extreme-dot", r: markedDot ? "5" : "3.2" }));
      group.addEventListener("mouseenter", function () {
        showTip(group, point, chart.unit);
      });
      group.addEventListener("mouseleave", scheduleHideTip);
      group.addEventListener("focus", function () {
        showTip(group, point, chart.unit);
      });
      group.addEventListener("blur", scheduleHideTip);
      group.addEventListener("click", function () {
        if (point.id) window.location.href = "/games/" + point.id;
      });
      group.addEventListener("keydown", function (event) {
        if (event.key === "Enter" && point.id) window.location.href = "/games/" + point.id;
      });
      svg.appendChild(group);
    });
  }

  function dateTime(value) {
    if (!value) return 0;
    return Date.parse(value + "T12:00:00Z") || 0;
  }

  function jitter(id) {
    return ((Number(id) * 17) % 7) - 3;
  }

  function yearTicks(minT, maxT) {
    const start = new Date(minT).getUTCFullYear();
    const end = new Date(maxT).getUTCFullYear();
    const years = [];
    const step = end - start > 12 ? 4 : end - start > 6 ? 2 : 1;
    for (let year = start; year <= end; year += step) {
      years.push(Date.UTC(year, 0, 1));
    }
    return years;
  }

  function valueTicks(minV, maxV, unit) {
    const span = maxV - minV;
    const rough = span / 4;
    const pow = Math.pow(10, Math.floor(Math.log10(rough || 1)));
    const n = rough / pow;
    const step = n <= 1 ? pow : n <= 2 ? 2 * pow : n <= 5 ? 5 * pow : 10 * pow;
    const ticks = [];
    const first = Math.ceil(minV / step) * step;
    for (let value = first; value <= maxV + step / 10; value += step) {
      ticks.push(unit === "fans" ? Math.round(value) : Math.round(value * 10) / 10);
    }
    return ticks;
  }

  function axisValue(value, unit) {
    if (unit === "fans") return compactNumber(value);
    return String(Math.round(value));
  }

  function showTip(hit, point, unit) {
    clearHideTip();
    scoreEl.textContent = point.score || "Game";
    if (point.id) {
      scoreEl.href = "/games/" + point.id;
    } else {
      scoreEl.removeAttribute("href");
    }
    valueEl.textContent = formatValue(point.value, unit);
    dateEl.textContent = point.date || "";
    dateEl.hidden = !point.date;
    const extra = [point.venue, point.detail].filter(Boolean).join(" · ");
    venueEl.textContent = extra;
    venueEl.hidden = !extra;
    tooltip.hidden = false;
    const wrapBox = wrap.getBoundingClientRect();
    const hitBox = hit.getBoundingClientRect();
    const tipWidth = tooltip.offsetWidth;
    const tipHeight = tooltip.offsetHeight;
    let left = hitBox.left - wrapBox.left + hitBox.width / 2 - tipWidth / 2;
    let top = hitBox.top - wrapBox.top - tipHeight - 10;
    left = Math.max(8, Math.min(left, wrapBox.width - tipWidth - 8));
    if (top < 8) top = hitBox.bottom - wrapBox.top + 10;
    tooltip.style.left = left + "px";
    tooltip.style.top = top + "px";
  }

  function clearHideTip() {
    if (hideTimer) {
      window.clearTimeout(hideTimer);
      hideTimer = null;
    }
  }

  function scheduleHideTip() {
    clearHideTip();
    hideTimer = window.setTimeout(function () {
      tooltip.hidden = true;
    }, 160);
  }
}
})();
