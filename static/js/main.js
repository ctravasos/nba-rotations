// Toggles (Season / L10, In / Out), table sorting, and text / min-GP filters.

document.querySelectorAll(".toggle").forEach((group) => {
  const name = group.dataset.toggle;
  group.querySelectorAll("button").forEach((btn) => {
    btn.addEventListener("click", () => {
      group.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b === btn));
      document.querySelectorAll(`[data-panel="${name}"]`).forEach((panel) => {
        panel.hidden = panel.dataset.name !== btn.dataset.show;
      });
    });
  });
});

document.querySelectorAll("table.sortable").forEach((table) => {
  table.querySelectorAll("th[data-sort]").forEach((th) => {
    th.addEventListener("click", () => {
      const idx = [...th.parentNode.children].indexOf(th);
      const numeric = th.dataset.sort === "num";
      const desc = !th.classList.contains("sorted-desc");  // first click on a column sorts high -> low
      table.querySelectorAll("th").forEach((h) => h.classList.remove("sorted-asc", "sorted-desc"));
      th.classList.add(desc ? "sorted-desc" : "sorted-asc");
      const body = table.tBodies[0];
      const value = (row) => {
        const cell = row.children[idx];
        return numeric ? parseFloat(cell.dataset.v ?? cell.textContent) || 0 : cell.textContent.trim().toLowerCase();
      };
      const rows = [...body.rows].sort((a, b) => {
        const [x, y] = [value(a), value(b)];
        const cmp = numeric ? x - y : x.localeCompare(y);
        return desc ? -cmp : cmp;
      });
      body.append(...rows);
    });
  });
});

function applyFilters(tableName) {
  const input = document.querySelector(`[data-filter-table="${tableName}"]`);
  const minGp = document.querySelector(`[data-min-gp="${tableName}"]`);
  const q = input ? input.value.trim().toLowerCase() : "";
  document.querySelectorAll(`table[data-table="${tableName}"] tbody tr`).forEach((row) => {
    const textOk = !q || row.textContent.toLowerCase().includes(q);
    const gpOk = !minGp || !minGp.checked || Number(row.dataset.gp) >= 5;
    row.hidden = !(textOk && gpOk);
  });
}

document.querySelectorAll("[data-filter-table], [data-min-gp]").forEach((el) => {
  const name = el.dataset.filterTable || el.dataset.minGp;
  el.addEventListener("input", () => applyFilters(name));
  applyFilters(name);
});

// Today page: the build ships yesterday / today / tomorrow; show the visitor's current
// Eastern-time date (falls back to the latest earlier day, then the first).
const days = [...document.querySelectorAll("section.day[data-date]")];
if (days.length) {
  const today = new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York" }).format(new Date());
  const pick = days.find((d) => d.dataset.date === today)
    || days.filter((d) => d.dataset.date <= today).pop() || days[0];
  days.forEach((d) => { d.hidden = d !== pick; });
}
