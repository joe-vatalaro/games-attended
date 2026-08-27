(function () {
  document.querySelectorAll("[data-sortable]").forEach(function (table) {
    const headers = Array.prototype.slice.call(table.querySelectorAll("thead th[data-sort]"));
    headers.forEach(function (th, colIndex) {
      th.addEventListener("click", function () {
        const current = th.getAttribute("aria-sort");
        const next = current === "descending" ? "ascending" : "descending";
        headers.forEach(function (other) {
          other.removeAttribute("aria-sort");
        });
        th.setAttribute("aria-sort", next);
        sortTable(table, colIndex, next === "ascending" ? 1 : -1);
      });
    });
  });

  function sortTable(table, colIndex, direction) {
    const tbody = table.querySelector("tbody");
    const rows = Array.prototype.slice.call(tbody.rows);
    rows.sort(function (a, b) {
      const av = cellValue(a.cells[colIndex]);
      const bv = cellValue(b.cells[colIndex]);
      if (av === bv) return 0;
      if (av === null) return 1;
      if (bv === null) return -1;
      if (typeof av === "number" && typeof bv === "number") {
        return (av - bv) * direction;
      }
      return String(av).localeCompare(String(bv), undefined, { numeric: true, sensitivity: "base" }) * direction;
    });
    rows.forEach(function (row) {
      tbody.appendChild(row);
    });
  }

  function cellValue(cell) {
    if (!cell) return null;
    if (Object.prototype.hasOwnProperty.call(cell.dataset, "value") && cell.dataset.value !== "") {
      const numeric = Number(cell.dataset.value);
      return Number.isNaN(numeric) ? cell.dataset.value.toLowerCase() : numeric;
    }
    const text = cell.textContent.replace(/\s+/g, " ").trim();
    if (!text || text === "—") return null;
    const numeric = Number(text.replace(/,/g, ""));
    if (!Number.isNaN(numeric) && /^-?[\d.,]+$/.test(text)) return numeric;
    return text.toLowerCase();
  }
})();
