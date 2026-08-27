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

  show(tabFromLocation(), false);
})();
