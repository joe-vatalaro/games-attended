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

  function initCollapsibleTables() {
  document.querySelectorAll(".report-panel table").forEach(function (table) {
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
})();
