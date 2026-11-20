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

// Game page "Key Players": pick players from both rosters; only their rotation-map rows show.
// Picks are saved per game in localStorage and in the URL (#players=id,id) so a link shares them.
document.querySelectorAll(".player-picker").forEach((picker) => {
  const storeKey = `keyplayers:${picker.dataset.picker}`;
  const panel = picker.querySelector(".picker-panel");
  const chips = picker.querySelector(".picker-chips");
  const boxes = [...picker.querySelectorAll('input[type="checkbox"]')];
  const search = picker.querySelector(".picker-search");

  function render() {
    const chosen = boxes.filter((b) => b.checked).map((b) => b.value);
    localStorage.setItem(storeKey, JSON.stringify(chosen));
    history.replaceState(null, "", chosen.length ? `#players=${chosen.join(",")}` : location.pathname);
    chips.replaceChildren(...boxes.filter((b) => b.checked).map((b) => {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "chip";
      chip.textContent = `${b.dataset.label} ×`;
      chip.addEventListener("click", () => { b.checked = false; render(); });
      return chip;
    }));
    document.querySelectorAll("table.keymap").forEach((table) => {
      table.querySelectorAll("tr[data-pid]").forEach((row) => { row.hidden = !chosen.includes(row.dataset.pid); });
      table.querySelector(".keymap-empty").hidden = chosen.length > 0;
    });
  }

  let saved = [];
  const fromUrl = location.hash.match(/players=([\d,]+)/);
  if (fromUrl) saved = fromUrl[1].split(",");
  else try { saved = JSON.parse(localStorage.getItem(storeKey) || "[]"); } catch { saved = []; }
  boxes.forEach((b) => { b.checked = saved.includes(b.value); b.addEventListener("change", render); });
  picker.querySelector(".picker-toggle").addEventListener("click", () => {
    panel.hidden = !panel.hidden;
    if (!panel.hidden) search.focus();
  });
  search.addEventListener("input", () => {
    const q = search.value.trim().toLowerCase();
    picker.querySelectorAll(".picker-item").forEach((item) => { item.hidden = !!q && !item.dataset.search.includes(q); });
  });
  document.addEventListener("click", (e) => { if (!picker.contains(e.target)) panel.hidden = true; });
  render();
});
