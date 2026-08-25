/* Minimal SVG chart primitives — no external libraries, no CDN.
 *
 * Encoding rules held constant across every chart here:
 *   · P&L is a diverging quantity, so it uses one warm/cool pair (blue gain,
 *     red loss) with a neutral midpoint. Green/red is deliberately avoided:
 *     it is the pair red-green colour blindness cannot separate.
 *   · Colour never carries meaning alone — every mark has a signed number in
 *     its tooltip, its axis label, or the table view beside it.
 */
const Charts = (() => {
  const NS = 'http://www.w3.org/2000/svg';
  const AXIS_BAND = 26;      // room reserved for x labels below the plot
  const GAP = 2;             // surface gap between adjacent bars

  let tip;

  function tooltip() {
    if (!tip) {
      tip = document.createElement('div');
      tip.className = 'tooltip';
      document.body.appendChild(tip);
    }
    return tip;
  }

  function showTip(html, event) {
    const el = tooltip();
    el.innerHTML = html;
    el.dataset.show = 'true';
    const pad = 14;
    const rect = el.getBoundingClientRect();
    let x = event.clientX + pad;
    let y = event.clientY + pad;
    if (x + rect.width > window.innerWidth - 8) x = event.clientX - rect.width - pad;
    if (y + rect.height > window.innerHeight - 8) y = event.clientY - rect.height - pad;
    el.style.left = `${Math.max(8, x)}px`;
    el.style.top = `${Math.max(8, y)}px`;
  }

  function hideTip() {
    if (tip) tip.dataset.show = 'false';
  }

  function el(name, attrs = {}, parent) {
    const node = document.createElementNS(NS, name);
    for (const [key, value] of Object.entries(attrs)) {
      if (value === undefined || value === null) continue;
      node.setAttribute(key, String(value));
    }
    if (parent) parent.appendChild(node);
    return node;
  }

  function svgFor(host, height) {
    host.innerHTML = '';
    const width = Math.max(320, host.clientWidth || host.parentElement.clientWidth || 640);
    const svg = el('svg', {
      class: 'chart', width: '100%', height,
      viewBox: `0 0 ${width} ${height}`, preserveAspectRatio: 'none',
      role: 'img',
    }, host);
    svg.addEventListener('mouseleave', hideTip);
    return { svg, width, height };
  }

  /* Re-render on container resize so text never scales with the viewBox. */
  function responsive(host, draw) {
    draw();
    if (host._ro) host._ro.disconnect();
    let last = host.clientWidth;
    host._ro = new ResizeObserver(() => {
      if (Math.abs(host.clientWidth - last) < 12) return;
      last = host.clientWidth;
      draw();
    });
    host._ro.observe(host);
  }

  function niceTicks(min, max, count = 4) {
    if (min === max) { min -= 1; max += 1; }
    const span = max - min;
    const raw = span / count;
    const mag = Math.pow(10, Math.floor(Math.log10(Math.abs(raw) || 1)));
    const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= raw) || mag * 10;
    const ticks = [];
    for (let v = Math.ceil(min / step) * step; v <= max + 1e-9; v += step) ticks.push(v);
    return ticks;
  }

  const money = (v, currency = '') => {
    const sign = v > 0 ? '+' : v < 0 ? '−' : '';
    const abs = Math.abs(v);
    const digits = abs >= 1000 ? 0 : abs >= 100 ? 1 : 2;
    return `${sign}${abs.toLocaleString('ru-RU', {
      minimumFractionDigits: digits, maximumFractionDigits: digits,
    })}${currency ? ' ' + currency : ''}`;
  };

  const compact = v => {
    const abs = Math.abs(v);
    if (abs >= 1e6) return (v / 1e6).toFixed(1).replace('.0', '') + 'M';
    if (abs >= 1e3) return (v / 1e3).toFixed(1).replace('.0', '') + 'k';
    return Math.round(v).toString();
  };

  /** Bar path with the data end rounded (4px) and the baseline end square. */
  function barPath(x, y, w, h, side) {
    const r = Math.min(4, w / 2, h / 2);
    if (h <= 0 || w <= 0) return '';
    switch (side) {
      case 'up':    return `M${x} ${y + h}V${y + r}a${r} ${r} 0 0 1 ${r} -${r}h${w - 2 * r}a${r} ${r} 0 0 1 ${r} ${r}V${y + h}Z`;
      case 'down':  return `M${x} ${y}V${y + h - r}a${r} ${r} 0 0 0 ${r} ${r}h${w - 2 * r}a${r} ${r} 0 0 0 ${r} -${r}V${y}Z`;
      case 'right': return `M${x} ${y}h${w - r}a${r} ${r} 0 0 1 ${r} ${r}v${h - 2 * r}a${r} ${r} 0 0 1 -${r} ${r}H${x}Z`;
      default:      return `M${x + w} ${y}H${x + r}a${r} ${r} 0 0 0 -${r} ${r}v${h - 2 * r}a${r} ${r} 0 0 0 ${r} ${r}h${w - r}Z`;
    }
  }

  /**
   * Equity curve: one balance line over a wash showing distance below the
   * running peak. Two marks, one measure, one y-axis.
   */
  function equity(host, points, opts = {}) {
    const currency = opts.currency || '';
    responsive(host, () => {
      const height = opts.height || 300;
      const { svg, width } = svgFor(host, height);
      if (!points.length) return emptyNote(svg, width, height);

      const padL = 56, padR = 14, padT = 12, padB = AXIS_BAND;
      const plotW = width - padL - padR;
      const plotH = height - padT - padB;

      const times = points.map(p => new Date(p.close_time).getTime());
      const t0 = times[0], t1 = times[times.length - 1] || t0 + 1;
      const values = points.flatMap(p => [p.equity, p.peak]);
      const lo = Math.min(...values, opts.opening ?? Infinity);
      const hi = Math.max(...values, opts.opening ?? -Infinity);
      const ticks = niceTicks(lo, hi, 4);
      const yLo = Math.min(lo, ticks[0]);
      const yHi = Math.max(hi, ticks[ticks.length - 1]);

      const X = t => padL + (t1 === t0 ? plotW / 2 : ((t - t0) / (t1 - t0)) * plotW);
      const Y = v => padT + plotH - ((v - yLo) / (yHi - yLo || 1)) * plotH;

      for (const tick of ticks) {
        el('line', { class: 'grid-line', x1: padL, x2: width - padR, y1: Y(tick), y2: Y(tick) }, svg);
        el('text', { x: padL - 8, y: Y(tick) + 4, 'text-anchor': 'end' }, svg)
          .textContent = compact(tick);
      }

      const peakPath = points.map((p, i) => `${i ? 'L' : 'M'}${X(times[i])} ${Y(p.peak)}`).join('');
      const backPath = points.map((p, i) => `L${X(times[points.length - 1 - i])} ${Y(points[points.length - 1 - i].equity)}`).join('');
      el('path', {
        d: `${peakPath}${backPath}Z`, fill: 'var(--loss)', 'fill-opacity': .10, stroke: 'none',
      }, svg);

      el('path', {
        d: points.map((p, i) => `${i ? 'L' : 'M'}${X(times[i])} ${Y(p.equity)}`).join(''),
        fill: 'none', stroke: 'var(--profit)', 'stroke-width': 2,
        'stroke-linejoin': 'round', 'stroke-linecap': 'round',
      }, svg);

      if (opts.opening !== undefined) {
        el('line', {
          class: 'axis-line', x1: padL, x2: width - padR, y1: Y(opts.opening), y2: Y(opts.opening),
        }, svg);
      }

      const fmtDate = d => d.toLocaleDateString('ru-RU', { day: '2-digit', month: 'short', year: '2-digit' });
      const tickCount = Math.max(2, Math.min(6, Math.floor(plotW / 130)));
      for (let i = 0; i < tickCount; i++) {
        const t = t0 + ((t1 - t0) * i) / (tickCount - 1);
        const anchor = i === 0 ? 'start' : i === tickCount - 1 ? 'end' : 'middle';
        el('text', { x: X(t), y: height - 8, 'text-anchor': anchor }, svg)
          .textContent = fmtDate(new Date(t));
      }

      // Direct label on the endpoint only — never a number on every point.
      const endValue = points[points.length - 1].equity;
      el('circle', {
        cx: X(times[times.length - 1]), cy: Y(endValue), r: 4,
        fill: 'var(--profit)', stroke: 'var(--surface)', 'stroke-width': 2,
      }, svg);

      const marker = el('circle', {
        r: 5, fill: 'var(--profit)', stroke: 'var(--surface)', 'stroke-width': 2, opacity: 0,
      }, svg);
      const crosshair = el('line', { class: 'axis-line', y1: padT, y2: padT + plotH, opacity: 0 }, svg);

      el('rect', { x: padL, y: padT, width: plotW, height: plotH, fill: 'transparent' }, svg)
        .addEventListener('mousemove', event => {
          const box = svg.getBoundingClientRect();
          const px = ((event.clientX - box.left) / box.width) * width;
          const target = t0 + ((px - padL) / plotW) * (t1 - t0);
          let index = 0, best = Infinity;
          for (let i = 0; i < times.length; i++) {
            const d = Math.abs(times[i] - target);
            if (d < best) { best = d; index = i; }
          }
          const point = points[index];
          const x = X(times[index]);
          marker.setAttribute('cx', x); marker.setAttribute('cy', Y(point.equity));
          marker.setAttribute('opacity', 1);
          crosshair.setAttribute('x1', x); crosshair.setAttribute('x2', x);
          crosshair.setAttribute('opacity', 1);
          showTip(`
            <div class="t-title">${new Date(point.close_time).toLocaleString('ru-RU')}</div>
            <div class="t-row"><span>${point.symbol} · сделка</span><b>${money(point.net, currency)}</b></div>
            <div class="t-row"><span>Баланс</span><b>${point.equity.toLocaleString('ru-RU')} ${currency}</b></div>
            <div class="t-row"><span>Просадка от пика</span><b>${money(point.drawdown, currency)} (${point.drawdown_pct.toFixed(1)}%)</b></div>`, event);
        });
      svg.addEventListener('mouseleave', () => {
        marker.setAttribute('opacity', 0);
        crosshair.setAttribute('opacity', 0);
      });
    });
  }

  /**
   * Sign-coloured bars for any label→P&L breakdown.
   * `horizontal: true` for long labels (symbols); vertical otherwise.
   */
  function bars(host, rows, opts = {}) {
    const currency = opts.currency || '';
    const valueOf = opts.value || (r => r.net);
    const labelOf = opts.label || (r => r.label);
    // Sign polarity by default; a chart whose bars are counts passes its own rule.
    const colorOf = opts.color || (v => (v >= 0 ? 'var(--profit)' : 'var(--loss)'));
    // Few categories deserve wider bars; many need thin ones.
    const MAX_BAR = opts.maxBar || 0;
    responsive(host, () => {
      const horizontal = !!opts.horizontal;
      const height = opts.height || (horizontal ? Math.max(140, rows.length * 30 + 30) : 240);
      const { svg, width } = svgFor(host, height);
      if (!rows.length) return emptyNote(svg, width, height);

      const values = rows.map(valueOf);
      const maxAbs = Math.max(1e-9, ...values.map(Math.abs));

      if (horizontal) {
        const padL = opts.labelWidth || 84, padR = 16, padT = 6, padB = 6;
        const plotW = width - padL - padR;
        const rowH = (height - padT - padB) / rows.length;
        const zeroX = padL + plotW / 2;
        // Reserve a gutter wide enough for the longest value label on both sides,
        // so a full-length bar never pushes its own number into the category names.
        const widest = Math.max(...values.map(v => money(v, '').length)) * 6.4 + 22;
        const half = Math.max(24, plotW / 2 - widest);
        const X = v => (v / maxAbs) * half;

        el('line', { class: 'axis-line', x1: zeroX, x2: zeroX, y1: padT, y2: height - padB }, svg);

        rows.forEach((row, i) => {
          const value = valueOf(row);
          const barH = Math.max(6, rowH - GAP * 3);
          const y = padT + i * rowH + (rowH - barH) / 2;
          const w = Math.max(2, Math.abs(X(value)));
          const x = value >= 0 ? zeroX : zeroX - w;
          const node = el('path', {
            class: 'mark',
            d: barPath(x, y, w, barH, value >= 0 ? 'right' : 'left'),
            fill: colorOf(value, row),
          }, svg);
          bindTip(node, row, value, labelOf, currency, opts);

          el('text', { x: padL - 10, y: y + barH / 2 + 4, 'text-anchor': 'end', class: 'label-strong' }, svg)
            .textContent = labelOf(row);

          // Value sits at the bar's outer end, but a long negative bar would run
          // it into the category gutter — put it inside the bar instead.
          const text = money(value, '');
          const textW = text.length * 6.4 + 10;
          const outside = value >= 0
            ? zeroX + w + 8 + textW < width - 2
            : zeroX - w - 8 - textW > padL;
          el('text', {
            x: outside
              ? (value >= 0 ? zeroX + w + 8 : zeroX - w - 8)
              : (value >= 0 ? zeroX + w - 8 : zeroX - w + 8),
            y: y + barH / 2 + 4,
            'text-anchor': (value >= 0) === outside ? 'start' : 'end',
            fill: outside ? undefined : 'var(--surface)',
          }, svg).textContent = text;
        });
        return;
      }

      const padL = 48, padR = 10, padT = 10, padB = AXIS_BAND;
      const plotW = width - padL - padR;
      const plotH = height - padT - padB;
      const ticks = niceTicks(Math.min(0, ...values), Math.max(0, ...values), 4);
      const yLo = Math.min(0, ...ticks, ...values);
      const yHi = Math.max(0, ...ticks, ...values);
      const Y = v => padT + plotH - ((v - yLo) / (yHi - yLo || 1)) * plotH;
      const slot = plotW / rows.length;
      // Spec cap: a bar never exceeds 24px, and never fills its slot.
      const cap = MAX_BAR || 24;
      const barW = Math.min(cap, Math.max(3, slot - GAP * 2));

      for (const tick of ticks) {
        el('line', { class: 'grid-line', x1: padL, x2: width - padR, y1: Y(tick), y2: Y(tick) }, svg);
        el('text', { x: padL - 8, y: Y(tick) + 4, 'text-anchor': 'end' }, svg).textContent = compact(tick);
      }
      el('line', { class: 'axis-line', x1: padL, x2: width - padR, y1: Y(0), y2: Y(0) }, svg);

      // Show every Nth label, where N is whatever keeps them from touching.
      const labelPx = Math.max(...rows.map(r => String(labelOf(r)).length)) * 6.4 + 10;
      const every = Math.max(1, Math.ceil(labelPx / slot));
      rows.forEach((row, i) => {
        const value = valueOf(row);
        const x = padL + i * slot + (slot - barW) / 2;
        const top = value >= 0 ? Y(value) : Y(0);
        const h = Math.max(1, Math.abs(Y(value) - Y(0)));
        const node = el('path', {
          class: 'mark',
          d: barPath(x, top, barW, h, value >= 0 ? 'up' : 'down'),
          fill: colorOf(value, row),
        }, svg);
        bindTip(node, row, value, labelOf, currency, opts);

        if (i % every === 0) {
          el('text', { x: x + barW / 2, y: height - 8, 'text-anchor': 'middle' }, svg)
            .textContent = labelOf(row);
        }
      });
    });
  }

  function bindTip(node, row, value, labelOf, currency, opts) {
    const extra = opts.tooltip ? opts.tooltip(row) : '';
    node.addEventListener('mousemove', event => showTip(`
      <div class="t-title">${labelOf(row)}</div>
      <div class="t-row"><span>${opts.valueLabel || 'Результат'}</span><b>${money(value, currency)}</b></div>
      ${extra}`, event));
    node.addEventListener('mouseleave', hideTip);
  }

  /** Day-by-day P&L as a calendar heatmap: diverging, neutral grey at zero. */
  function calendar(host, days, opts = {}) {
    const currency = opts.currency || '';
    host.innerHTML = '';
    if (!days.length) {
      host.innerHTML = '<p class="hint">Нет данных за период.</p>';
      return;
    }
    const cell = 13, gap = 3, top = 18, left = 30;
    const byDate = new Map(days.map(d => [d.date, d.net]));
    const first = new Date(days[0].date + 'T00:00:00Z');
    const last = new Date(days[days.length - 1].date + 'T00:00:00Z');
    // Start on the Monday of the first week so weekday rows line up.
    const start = new Date(first);
    start.setUTCDate(start.getUTCDate() - ((start.getUTCDay() + 6) % 7));
    const weeks = Math.ceil(((last - start) / 86400000 + 1) / 7);
    const width = left + weeks * (cell + gap) + 10;
    const height = top + 7 * (cell + gap) + 20;
    const maxAbs = Math.max(1e-9, ...days.map(d => Math.abs(d.net)));

    const svg = el('svg', {
      class: 'chart', width, height, viewBox: `0 0 ${width} ${height}`, role: 'img',
      // `.chart { width: 100% }` would stretch and re-centre the grid; pin it.
      style: `width:${width}px;max-width:none`,
    }, host);
    svg.addEventListener('mouseleave', hideTip);

    ['Пн', '', 'Ср', '', 'Пт', '', 'Вс'].forEach((name, row) => {
      if (!name) return;
      el('text', { x: left - 8, y: top + row * (cell + gap) + cell - 2, 'text-anchor': 'end' }, svg)
        .textContent = name;
    });

    let lastMonth = -1;
    for (let week = 0; week < weeks; week++) {
      for (let row = 0; row < 7; row++) {
        const day = new Date(start);
        day.setUTCDate(start.getUTCDate() + week * 7 + row);
        if (day > last) continue;
        const key = day.toISOString().slice(0, 10);
        const value = byDate.get(key);
        const x = left + week * (cell + gap);
        const y = top + row * (cell + gap);

        if (row === 0 && day.getUTCMonth() !== lastMonth && day <= last) {
          lastMonth = day.getUTCMonth();
          el('text', { x, y: top - 6 }, svg).textContent =
            day.toLocaleDateString('ru-RU', { month: 'short', timeZone: 'UTC' });
        }

        const intensity = value === undefined ? 0 : 0.25 + 0.75 * Math.sqrt(Math.abs(value) / maxAbs);
        const node = el('rect', {
          class: 'mark', x, y, width: cell, height: cell, rx: 3,
          fill: value === undefined ? 'var(--neutral)' : value >= 0 ? 'var(--profit)' : 'var(--loss)',
          'fill-opacity': value === undefined ? 1 : intensity,
        }, svg);

        const title = day.toLocaleDateString('ru-RU', { day: '2-digit', month: 'long', year: 'numeric', timeZone: 'UTC' });
        node.addEventListener('mousemove', event => showTip(
          `<div class="t-title">${title}</div><div class="t-row"><span>Итог дня</span><b>${
            value === undefined ? 'нет сделок' : money(value, currency)}</b></div>`, event));
        node.addEventListener('mouseleave', hideTip);
      }
    }
  }

  function emptyNote(svg, width, height) {
    el('text', { x: width / 2, y: height / 2, 'text-anchor': 'middle' }, svg)
      .textContent = 'Нет данных за выбранный период';
  }

  /* ---------------------------------------------------------------
   * Small forms: a sparkline, a meter, a run of outcomes, a month grid,
   * a distribution. Each is a figure, not a chart with axes — they live
   * inside tiles and cards where chrome would cost more than it explains.
   * ------------------------------------------------------------- */

  /** 
   * Trend behind a stat tile. No axes, no labels — the tile's value carries
   * the number; this only carries the shape.
   */
  function sparkline(host, values, opts = {}) {
    responsive(host, () => {
      const height = opts.height || 40;
      const { svg, width } = svgFor(host, height);
      if (values.length < 2) return;

      const pad = 3;
      const lo = Math.min(...values);
      const hi = Math.max(...values);
      const X = i => pad + (i / (values.length - 1)) * (width - pad * 2);
      const Y = v => height - pad - ((v - lo) / (hi - lo || 1)) * (height - pad * 2);
      const stroke = opts.color || 'var(--profit)';

      const line = values.map((v, i) => `${i ? 'L' : 'M'}${X(i)} ${Y(v)}`).join('');
      if (opts.fill !== false) {
        el('path', {
          d: `${line}L${X(values.length - 1)} ${height}L${X(0)} ${height}Z`,
          fill: stroke, 'fill-opacity': .10, stroke: 'none',
        }, svg);
      }
      el('path', {
        d: line, fill: 'none', stroke, 'stroke-width': 2,
        'stroke-linejoin': 'round', 'stroke-linecap': 'round',
      }, svg);
      el('circle', {
        cx: X(values.length - 1), cy: Y(values[values.length - 1]), r: 3.5,
        fill: stroke, stroke: 'var(--surface)', 'stroke-width': 2,
      }, svg);
    });
  }

  /**
   * A ratio against its whole. The unfilled track is a lighter step of the
   * same hue, so the state reads across the entire bar rather than only the
   * filled part — and it is never a two-slice pie.
   */
  function meter(host, fraction, opts = {}) {
    const pct = Math.max(0, Math.min(1, fraction || 0));
    const color = opts.color || 'var(--profit)';
    host.innerHTML = `
      <div class="meter" role="img" aria-label="${opts.label || ''} ${Math.round(pct * 100)}%">
        <div class="meter-track" style="--meter-hue:${color}">
          <div class="meter-fill" style="width:${(pct * 100).toFixed(1)}%;background:${color}"></div>
        </div>
      </div>`;
  }

  /**
   * The run of recent outcomes, newest last. Each trade is one thin bar off a
   * centre line — the shape of a streak is the thing being read, so the bars
   * stay narrow and unlabelled and the tooltip carries the detail.
   */
  function formStrip(host, trades, opts = {}) {
    const currency = opts.currency || '';
    responsive(host, () => {
      const height = opts.height || 64;
      const { svg, width } = svgFor(host, height);
      if (!trades.length) return emptyNote(svg, width, height);

      const maxAbs = Math.max(1e-9, ...trades.map(t => Math.abs(t.net)));
      const slot = width / trades.length;
      const barW = Math.max(2, Math.min(10, slot - GAP));
      const mid = height / 2;
      const reach = mid - 6;

      el('line', { class: 'axis-line', x1: 0, x2: width, y1: mid, y2: mid }, svg);

      trades.forEach((trade, i) => {
        const h = Math.max(2, (Math.abs(trade.net) / maxAbs) * reach);
        const x = i * slot + (slot - barW) / 2;
        const up = trade.net >= 0;
        const node = el('path', {
          class: 'mark',
          d: barPath(x, up ? mid - h : mid, barW, h, up ? 'up' : 'down'),
          fill: up ? 'var(--profit)' : 'var(--loss)',
        }, svg);
        node.addEventListener('mousemove', event => showTip(`
          <div class="t-title">${trade.symbol}</div>
          <div class="t-row"><span>Результат</span><b>${money(trade.net, currency)}</b></div>
          <div class="t-row"><span>Закрыта</span><b>${new Date(trade.close_time).toLocaleString('ru-RU')}</b></div>`,
          event));
        node.addEventListener('mouseleave', hideTip);
      });
    });
  }

  /**
   * Year × month grid of results. A grid of magnitudes is a heatmap; the sign
   * makes it diverging, so it takes the same blue/red pair with a neutral cell
   * for months that were never traded.
   */
  function monthMatrix(host, months, opts = {}) {
    const currency = opts.currency || '';
    host.innerHTML = '';
    if (!months.length) {
      host.innerHTML = '<p class="hint">Нет данных за период.</p>';
      return;
    }

    const NAMES = ['янв', 'фев', 'мар', 'апр', 'май', 'июн',
                   'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
    const byYear = new Map();
    for (const row of months) {
      const [year, month] = row.label.split('-').map(Number);
      if (!byYear.has(year)) byYear.set(year, new Array(12).fill(null));
      byYear.get(year)[month - 1] = row;
    }
    const years = [...byYear.keys()].sort();
    const maxAbs = Math.max(1e-9, ...months.map(r => Math.abs(r.net)));

    // A CSS grid rather than a table: table cells treat height as a minimum
    // and the row stretched unpredictably, which a grid track never does.
    const grid = document.createElement('div');
    grid.className = 'matrix';

    const head = document.createElement('div');
    head.className = 'matrix-row matrix-head';
    head.appendChild(cellDiv('matrix-year', ''));
    NAMES.forEach(name => head.appendChild(cellDiv('matrix-label', name)));
    head.appendChild(cellDiv('matrix-total-label', 'год'));
    grid.appendChild(head);

    for (const year of years) {
      const cells = byYear.get(year);
      const total = cells.reduce((sum, c) => sum + (c ? c.net : 0), 0);
      const row = document.createElement('div');
      row.className = 'matrix-row';
      row.appendChild(cellDiv('matrix-year', String(year)));

      cells.forEach((cell, index) => {
        const box = document.createElement('div');
        box.className = 'matrix-box';
        if (!cell) {
          // Not `empty`: that class belongs to the page-level empty state and
          // carries 64px of padding, which silently inflated every row.
          box.classList.add('matrix-none');
          box.title = `${NAMES[index]} ${year}: сделок не было`;
        } else {
          const intensity = 0.22 + 0.78 * Math.sqrt(Math.abs(cell.net) / maxAbs);
          box.style.background = cell.net >= 0 ? 'var(--profit)' : 'var(--loss)';
          box.style.opacity = intensity.toFixed(2);
          box.addEventListener('mousemove', event => showTip(`
            <div class="t-title">${NAMES[index]} ${year}</div>
            <div class="t-row"><span>Итог</span><b>${money(cell.net, currency)}</b></div>
            <div class="t-row"><span>Сделок</span><b>${cell.trades}</b></div>
            <div class="t-row"><span>Винрейт</span><b>${cell.win_rate}%</b></div>`, event));
          box.addEventListener('mouseleave', hideTip);
        }
        row.appendChild(box);
      });

      const totalCell = cellDiv(
        `matrix-total num ${total >= 0 ? 'pos' : 'neg'}`, money(total, ''));
      row.appendChild(totalCell);
      grid.appendChild(row);
    }

    host.appendChild(grid);
  }

  function cellDiv(className, text) {
    const node = document.createElement('div');
    node.className = className;
    node.textContent = text;
    return node;
  }

  /**
   * How trade results are spread. Bar height is a count, so the sign has to
   * come from which side of zero the bucket sits on.
   */
  function distribution(host, values, opts = {}) {
    const currency = opts.currency || '';
    responsive(host, () => {
      const height = opts.height || 190;
      const { svg, width } = svgFor(host, height);
      if (!values.length) return emptyNote(svg, width, height);

      const maxAbs = Math.max(...values.map(Math.abs));
      const binCount = 13;                       // odd, so zero sits on a boundary
      const step = (maxAbs * 2) / binCount;
      const bins = Array.from({ length: binCount }, (_, i) => ({
        from: -maxAbs + i * step,
        to: -maxAbs + (i + 1) * step,
        count: 0,
      }));
      for (const value of values) {
        const index = Math.min(binCount - 1, Math.max(0, Math.floor((value + maxAbs) / step)));
        bins[index].count += 1;
      }

      const padL = 34, padR = 8, padT = 8, padB = AXIS_BAND;
      const plotW = width - padL - padR;
      const plotH = height - padT - padB;
      const peak = Math.max(1, ...bins.map(b => b.count));
      const slot = plotW / binCount;
      const barW = Math.min(24, Math.max(3, slot - GAP));
      const Y = c => padT + plotH - (c / peak) * plotH;

      for (const tick of niceTicks(0, peak, 3)) {
        el('line', { class: 'grid-line', x1: padL, x2: width - padR, y1: Y(tick), y2: Y(tick) }, svg);
        el('text', { x: padL - 7, y: Y(tick) + 4, 'text-anchor': 'end' }, svg)
          .textContent = compact(tick);
      }
      el('line', { class: 'axis-line', x1: padL, x2: width - padR, y1: Y(0), y2: Y(0) }, svg);

      bins.forEach((bin, i) => {
        if (!bin.count) return;
        const x = padL + i * slot + (slot - barW) / 2;
        const h = Math.max(1, Y(0) - Y(bin.count));
        const node = el('path', {
          class: 'mark',
          d: barPath(x, Y(bin.count), barW, h, 'up'),
          fill: bin.to <= 0 ? 'var(--loss)' : 'var(--profit)',
        }, svg);
        node.addEventListener('mousemove', event => showTip(`
          <div class="t-title">${money(bin.from, '')} … ${money(bin.to, currency)}</div>
          <div class="t-row"><span>Сделок</span><b>${bin.count}</b></div>`, event));
        node.addEventListener('mouseleave', hideTip);
      });

      // Only the extremes and the centre are labelled — the tooltip has the rest.
      const zeroX = padL + plotW / 2;
      el('line', { class: 'axis-line', x1: zeroX, x2: zeroX, y1: padT, y2: padT + plotH }, svg);
      el('text', { x: padL, y: height - 8 }, svg).textContent = money(-maxAbs, '');
      el('text', { x: zeroX, y: height - 8, 'text-anchor': 'middle' }, svg).textContent = '0';
      el('text', { x: width - padR, y: height - 8, 'text-anchor': 'end' }, svg)
        .textContent = money(maxAbs, '');
    });
  }

  return {
    equity, bars, calendar, sparkline, meter, formStrip, monthMatrix, distribution,
    money, compact, hideTip,
  };
})();
