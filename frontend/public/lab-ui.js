/* Shared presentation for the two experiment pages. */
window.Lab = (() => {
  const number = new Intl.NumberFormat();
  const percent = value => value == null ? "—" : (100 * Number(value)).toFixed(1) + "%";
  const signed = (value, decimals = 2) => (value > 0 ? "+" : "") + Number(value).toFixed(decimals);

  function node(tag, text, className) {
    const element = document.createElement(tag);
    if (text != null) element.textContent = text;
    if (className) element.className = className;
    return element;
  }

  function row(body, values, selected = false) {
    const tr = body.insertRow();
    tr.classList.toggle("is-selected", selected);
    for (const value of values) {
      const cell = tr.insertCell();
      if (value instanceof Node) cell.append(value);
      else cell.textContent = value;
    }
    return tr;
  }

  function meter(value, text = percent(value), tone = "") {
    const item = node("span", text, "lab-meter " + tone);
    if (value == null) return item;
    const rail = node("span", null, "rail");
    const fill = node("i");
    fill.style.setProperty("--level", Math.max(0, Math.min(value, 1)) * 100 + "%");
    rail.setAttribute("aria-hidden", "true");
    rail.append(fill);
    item.append(rail);
    return item;
  }

  function roots(edge) {
    const description = node("span", null, "lab-source-id");
    for (const [role, arrow] of [["pre", ""], ["post", " → "]]) {
      const id = edge[role + "_id"];
      description.append(arrow);
      if (id != null) {
        const anchor = node("a", edge[role + "_root"]);
        const query = new URLSearchParams({dataset: "flywire", release: "783", neuron: id});
        anchor.href = "/#explorer?" + query;
        description.append(anchor);
      } else description.append(edge[role + "_root"]);
    }
    return description;
  }

  function stat(value, label) {
    const tile = node("div", null, "lab-stat");
    tile.append(node("strong", value), node("span", label));
    return tile;
  }

  function canvas(element) {
    const width = element.clientWidth;
    const height = element.clientHeight;
    if (width < 40 || height < 30) return null;
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    if (element.width !== Math.round(width * ratio) || element.height !== Math.round(height * ratio)) {
      element.width = Math.round(width * ratio);
      element.height = Math.round(height * ratio);
    }
    const ctx = element.getContext("2d");
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.fillStyle = "#122234";
    ctx.fillRect(0, 0, width, height);
    ctx.font = "11px system-ui";
    return {ctx, width, height};
  }

  function chart(element, spec) {
    const surface = canvas(element);
    if (!surface) return;
    const {ctx, width, height} = surface;
    const left = 42, right = width - 13, top = 18, bottom = height - 28;
    const [lo, hi] = spec.y;
    const x = value => left + value / Math.max(1, spec.end) * (right - left);
    const y = value => bottom - (value - lo) / (hi - lo) * (bottom - top);
    for (const [a,b] of spec.bands || []) {
      ctx.fillStyle = "#22384b";
      ctx.fillRect(x(a), top, x(b) - x(a), bottom - top);
    }
    for (const tick of spec.ticks) {
      const yp = y(tick);
      ctx.fillStyle = "#425568";
      ctx.fillRect(left, yp, right - left, 1);
      ctx.fillStyle = "#dbe6ef";
      ctx.textAlign = "right";
      ctx.fillText(spec.format(tick), left - 5, yp + 4);
    }
    ctx.textAlign = "center";
    ctx.fillStyle = "#dbe6ef";
    for (const [index, label] of spec.labels) ctx.fillText(label, x(index), height - 8);
    for (const series of spec.series) {
      ctx.strokeStyle = series.dashed ? "#ffcc82" : "#68d9ce";
      ctx.lineWidth = 2.6;
      ctx.setLineDash(series.dashed ? [5,4] : []);
      ctx.beginPath();
      series.points.forEach(([step,value], index) => {
        if (index === 0) ctx.moveTo(x(step), y(value));
        else ctx.lineTo(x(step), y(value));
      });
      ctx.stroke();
    }
    ctx.setLineDash([]);
    if (spec.cursor != null) {
      ctx.fillStyle = "#edf5ff";
      ctx.fillRect(x(spec.cursor), top, 1, bottom - top);
      for (const series of spec.series) {
        const point = series.points.find(([step]) => step === spec.cursor);
        if (!point) continue;
        ctx.fillStyle = series.dashed ? "#ffcc82" : "#68d9ce";
        ctx.beginPath();
        ctx.arc(x(point[0]), y(point[1]), 4, 0, 2 * Math.PI);
        ctx.fill();
      }
    }
    return {left, right, end: spec.end};
  }

  return {number, percent, signed, node, row, meter, roots, stat, canvas, chart};
})();
