/* Dashboard wiring: filters → /api/dashboard → panels. */
(() => {
  const $ = id => document.getElementById(id);
  const state = {
    filters: { from: '', to: '', symbol: '', magic: '' },
    currency: '',
    data: null,
    tradesOffset: 0,
    tradesSort: { key: 'close_time', desc: true },
  };

  const token = new URLSearchParams(location.search).get('token')
    || localStorage.getItem('tradingapp_token') || '';
  if (token) localStorage.setItem('tradingapp_token', token);

  async function api(path, options = {}) {
    const headers = { ...(options.headers || {}) };
    if (token) headers.Authorization = `Bearer ${token}`;
    const response = await fetch(path, { ...options, headers });
    if (!response.ok) {
      let detail = `Ошибка ${response.status}`;
      try { detail = (await response.json()).detail || detail; } catch { /* non-JSON body */ }
      throw new Error(detail);
    }
    return response.headers.get('content-type')?.includes('json') ? response.json() : response.text();
  }

  const money = (v, digits) => {
    if (v === null || v === undefined) return '—';
    const d = digits ?? (Math.abs(v) >= 1000 ? 0 : 2);
    return v.toLocaleString('ru-RU', { minimumFractionDigits: d, maximumFractionDigits: d });
  };
  const signed = v => (v === null || v === undefined ? '—' : Charts.money(v, state.currency));
  const signClass = v => (v > 0 ? 'pos' : v < 0 ? 'neg' : '');
  const pct = v => (v === null || v === undefined ? '—' : `${v.toFixed(1)}%`);
  const dt = iso => (iso ? new Date(iso).toLocaleString('ru-RU',
    { day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—');

  function duration(minutes) {
    if (minutes === null || minutes === undefined) return '—';
    if (minutes < 60) return `${Math.round(minutes)} мин`;
    if (minutes < 1440) return `${(minutes / 60).toFixed(1)} ч`;
    return `${(minutes / 1440).toFixed(1)} дн`;
  }

  /* ---------- generic table ---------- */
  function table(host, columns, rows, emptyText = 'Нет данных') {
    if (!rows.length) {
      host.innerHTML = `<tbody><tr><td colspan="${columns.length}" style="text-align:center;color:var(--muted)">${emptyText}</td></tr></tbody>`;
      return;
    }
    const head = columns.map(c =>
      `<th${c.sort ? ` data-sort="${c.sort}"` : ''}>${c.title}</th>`).join('');
    const body = rows.map(row => '<tr>' + columns.map(c => {
      const value = c.get(row);
      const cls = [c.cls ? c.cls(row) : '', c.numeric === false ? '' : 'num'].filter(Boolean).join(' ');
      return `<td class="${cls}">${value}</td>`;
    }).join('') + '</tr>').join('');
    host.innerHTML = `<thead><tr>${head}</tr></thead><tbody>${body}</tbody>`;
  }

  /* ---------- tiles ---------- */
  function tile(key, value, detail, cls = '') {
    return `<div class="tile"><span class="k">${key}</span>
      <span class="v ${cls}">${value}</span>
      <span class="d">${detail || ''}</span></div>`;
  }

  function renderTiles(s) {
    const cur = state.currency;
    $('tiles').innerHTML = [
      tile('Итог за период', signed(s.net_profit),
        `${s.trades} сделок · ${pct(s.return_pct)} к старту${s.opening_balance_estimated ? ' (баланс оценён)' : ''}`,
        signClass(s.net_profit)),
      tile('Винрейт', `${s.win_rate}%`, `${s.wins} прибыльных / ${s.losses} убыточных`),
      tile('Профит-фактор', s.profit_factor ?? '—',
        `Заработано ${money(s.gross_profit, 0)} ${cur} · потеряно ${money(s.gross_loss, 0)} ${cur}`,
        s.profit_factor >= 1 ? 'pos' : 'neg'),
      tile('Средний результат сделки', signed(s.expectancy),
        `+${money(s.avg_win, 0)} против ${money(s.avg_loss, 0)}`, signClass(s.expectancy)),
      tile('Максимальная просадка',
        `${s.max_drawdown > 0 ? '−' : ''}${money(s.max_drawdown, 0)} ${cur}`,
        `${s.max_drawdown_pct}% от пика · ${s.drawdown_recovered ? 'восстановлена' : 'ещё не отыграна'}`,
        'neg'),
      tile('Отношение выигрыша к проигрышу', s.payoff_ratio ?? '—',
        `Серии: ${s.streaks.longest_wins} побед / ${s.streaks.longest_losses} убытков подряд`),
      tile('Комиссии и свопы', signed(s.costs),
        `Комиссия ${money(s.commission, 0)} · своп ${money(s.swap, 0)}`,
        signClass(s.costs)),
      tile('Дни с прибылью', `${s.day_win_rate}%`,
        `${s.winning_days} из ${s.trading_days} торговых дней · ${s.avg_trades_per_day} сделки в день`),
    ].join('');
  }

  /* ---------- findings ---------- */
  const ICONS = { good: '✓', warn: '!', bad: '✕' };
  function renderFindings(host, findings) {
    host.innerHTML = findings.map(f => `
      <div class="finding" data-level="${f.level}">
        <span class="icon" aria-hidden="true">${ICONS[f.level] || '•'}</span>
        <span><span class="title">${f.title}</span><br>
        <span class="detail">${f.detail}</span></span>
      </div>`).join('');
  }

  /* ---------- panels ---------- */
  function renderOverview(d) {
    $('no-window-data').hidden = d.summary.trades > 0;
    renderTiles(d.summary);
    Charts.equity($('chart-equity'), d.equity, {
      currency: state.currency,
      opening: d.summary.opening_balance,
      height: 320,
    });
    renderFindings($('findings-short'), d.risk.findings.slice(0, 4));
    Charts.bars($('chart-months'), d.time.month, {
      currency: state.currency, height: 260,
      label: r => r.label.slice(2),
      tooltip: r => `<div class="t-row"><span>Сделок</span><b>${r.trades}</b></div>
                     <div class="t-row"><span>Винрейт</span><b>${r.win_rate}%</b></div>`,
    });
  }

  const SYMBOL_COLUMNS = [
    { title: 'Инструмент', get: r => r.label, numeric: false, cls: () => 'sym' },
    { title: 'Сделок', get: r => r.trades },
    { title: 'Винрейт', get: r => `${r.win_rate}%` },
    { title: 'Итог', get: r => signed(r.net), cls: r => signClass(r.net) },
    { title: 'ПФ', get: r => r.profit_factor ?? '—' },
    { title: 'Средняя сделка', get: r => signed(r.expectancy), cls: r => signClass(r.expectancy) },
    { title: 'Ср. прибыль', get: r => signed(r.avg_win), cls: () => 'pos' },
    { title: 'Ср. убыток', get: r => signed(r.avg_loss), cls: () => 'neg' },
    { title: 'Лучшая', get: r => signed(r.best) },
    { title: 'Худшая', get: r => signed(r.worst) },
    { title: 'Объём, лот', get: r => money(r.volume) },
    { title: 'Ср. время', get: r => duration(r.avg_duration_minutes) },
  ];

  function renderSymbols(d) {
    const rows = d.symbols.rows.filter(r => r.trades > 0);
    Charts.bars($('chart-symbols'), rows, {
      currency: state.currency, horizontal: true, labelWidth: 96,
      tooltip: r => `<div class="t-row"><span>Сделок</span><b>${r.trades}</b></div>
                     <div class="t-row"><span>Винрейт</span><b>${r.win_rate}%</b></div>
                     <div class="t-row"><span>Профит-фактор</span><b>${r.profit_factor ?? '—'}</b></div>`,
    });
    Charts.bars($('chart-sides'), d.symbols.by_side.filter(r => r.trades > 0), {
      currency: state.currency, height: 200,
      label: r => (r.label === 'buy' ? 'Покупки' : 'Продажи'),
      tooltip: r => `<div class="t-row"><span>Сделок</span><b>${r.trades}</b></div>
                     <div class="t-row"><span>Винрейт</span><b>${r.win_rate}%</b></div>`,
    });

    // A rank is honest whatever the sign: calling the second-best instrument
    // "лучший" while it still loses money would misread the table.
    const ranked = rows.map((r, i) => ({ ...r, tag: `№${i + 1}` }));
    const extremes = ranked.length > 6
      ? [...ranked.slice(0, 3), ...ranked.slice(-3)]
      : ranked;
    table($('table-extremes'), [
      { title: 'Место', get: r => r.tag, numeric: false },
      { title: 'Инструмент', get: r => r.label, numeric: false, cls: () => 'sym' },
      { title: 'Итог', get: r => signed(r.net), cls: r => signClass(r.net) },
      { title: 'Сделок', get: r => r.trades },
      { title: 'Винрейт', get: r => `${r.win_rate}%` },
    ], extremes);

    table($('table-symbols'), SYMBOL_COLUMNS, rows);
  }

  function renderTime(d) {
    const t = d.time;
    $('time-tiles').innerHTML = [
      t.best_weekday && tile('Лучший день недели', t.best_weekday.label,
        `${signed(t.best_weekday.net)} за ${t.best_weekday.trades} сделок`, 'pos'),
      t.worst_weekday && tile('Худший день недели', t.worst_weekday.label,
        `${signed(t.worst_weekday.net)} за ${t.worst_weekday.trades} сделок`, 'neg'),
      t.best_hour && tile('Лучший час входа', t.best_hour.label,
        `${signed(t.best_hour.net)} за ${t.best_hour.trades} сделок`, 'pos'),
      t.worst_hour && tile('Худший час входа', t.worst_hour.label,
        `${signed(t.worst_hour.net)} за ${t.worst_hour.trades} сделок`, 'neg'),
    ].filter(Boolean).join('');

    const tip = r => `<div class="t-row"><span>Сделок</span><b>${r.trades}</b></div>
                      <div class="t-row"><span>Винрейт</span><b>${r.win_rate}%</b></div>`;
    Charts.bars($('chart-weekday'), t.weekday, { currency: state.currency, height: 240, tooltip: tip });
    Charts.bars($('chart-hour'), t.hour, { currency: state.currency, height: 240, tooltip: tip });
    Charts.bars($('chart-duration'), t.duration.filter(r => r.trades > 0),
      { currency: state.currency, horizontal: true, labelWidth: 108, tooltip: tip });
    Charts.calendar($('chart-calendar'), t.daily, { currency: state.currency });

    $('scale-steps').innerHTML =
      [1, .62, .3].map(o => `<i style="background:var(--loss);opacity:${o}"></i>`).join('')
      + '<i style="background:var(--neutral)"></i>'
      + [.3, .62, 1].map(o => `<i style="background:var(--profit);opacity:${o}"></i>`).join('');

    table($('table-hours'), [
      { title: 'Час', get: r => r.label, numeric: false },
      { title: 'Сделок', get: r => r.trades },
      { title: 'Винрейт', get: r => `${r.win_rate}%` },
      { title: 'Итог', get: r => signed(r.net), cls: r => signClass(r.net) },
      { title: 'Средняя', get: r => signed(r.expectancy), cls: r => signClass(r.expectancy) },
    ], t.hour.filter(r => r.trades > 0));
  }

  function renderRisk(d) {
    const r = d.risk;
    renderFindings($('findings-full'), r.findings);

    const rm = r.r_multiples, sizing = r.sizing, stops = r.stops;
    $('risk-tiles').innerHTML = [
      tile('Сделок со стопом', `${stops.sl_coverage_pct}%`,
        `${stops.with_sl} из ${stops.trades} · без стопа ${signed(stops.net_without_sl)}`,
        stops.sl_coverage_pct >= 90 ? 'pos' : stops.sl_coverage_pct >= 60 ? '' : 'neg'),
      tile('Матожидание в R', rm.expectancy_r ?? '—',
        `По ${rm.sample_size} сделкам (${rm.coverage_pct}% истории)`,
        rm.expectancy_r > 0 ? 'pos' : rm.expectancy_r < 0 ? 'neg' : ''),
      tile('Ровность риска', { consistent: 'Ровно', variable: 'Плавает', erratic: 'Хаотично', unknown: '—' }[sizing.verdict],
        `Медианный риск ${money(sizing.median_risk, 0)} ${state.currency} · максимум ${money(sizing.max_risk, 0)}`,
        sizing.verdict === 'consistent' ? 'pos' : sizing.verdict === 'erratic' ? 'neg' : ''),
      tile('Держит убытки дольше', r.holding.loss_to_win_ratio ? `×${r.holding.loss_to_win_ratio}` : '—',
        `Убыток ${duration(r.holding.avg_loss_minutes)} против ${duration(r.holding.avg_win_minutes)}`,
        r.holding.holds_losers_longer ? 'neg' : 'pos'),
      tile('Стопы не удержали', `${r.stop_discipline.overruns}`,
        `из ${r.stop_discipline.losses_checked} убыточных · лишний убыток ${money(r.stop_discipline.excess_loss, 0)}`,
        r.stop_discipline.overruns ? 'neg' : 'pos'),
      tile('Отыгрыш после убытка', `${r.revenge.count}`,
        `Суммарно ${signed(r.revenge.net)} · средняя ${signed(r.revenge.avg_net)}`,
        r.revenge.hurts ? 'neg' : ''),
    ].join('');

    Charts.bars($('chart-r'), rm.histogram, {
      height: 240,
      value: row => row.count,
      valueLabel: 'Сделок',
      // Bar length is a count, so the sign has to come from the bucket itself:
      // everything left of 0R is a loss bucket.
      color: (_value, row) => (row.label.trimStart().startsWith('<') || row.label.includes('-')
        ? 'var(--loss)' : 'var(--profit)'),
    });

    table($('table-busy'), [
      { title: 'Дата', get: x => x.date, numeric: false },
      { title: 'Сделок', get: x => x.trades },
      { title: 'Итог дня', get: x => signed(x.net), cls: x => signClass(x.net) },
    ], r.overtrading.busy_days, 'Дней с перебором не найдено');

    table($('table-overruns'), [
      { title: 'Инструмент', get: x => x.symbol, numeric: false, cls: () => 'sym' },
      { title: 'Закрыта', get: x => dt(x.close_time), numeric: false },
      { title: 'План риска', get: x => money(x.planned_risk) },
      { title: 'Факт убытка', get: x => money(x.actual_loss), cls: () => 'neg' },
      { title: 'Перебор', get: x => `+${x.overrun_pct}%`, cls: () => 'neg' },
    ], r.stop_discipline.worst, 'Все убытки уложились в план — это хорошо');

    table($('table-oversized'), [
      { title: 'Инструмент', get: x => x.symbol, numeric: false, cls: () => 'sym' },
      { title: 'Закрыта', get: x => dt(x.close_time), numeric: false },
      { title: 'Риск', get: x => money(x.risk) },
      { title: 'Результат', get: x => signed(x.net), cls: x => signClass(x.net) },
    ], sizing.oversized_trades, 'Крупных отклонений по риску нет');
  }

  /* ---------- trades panel ---------- */
  const TRADE_COLUMNS = [
    { title: 'Инструмент', get: t => t.symbol, numeric: false, cls: () => 'sym', sort: 'symbol' },
    { title: 'Направление', get: t => (t.side === 'buy' ? 'Покупка' : 'Продажа'), numeric: false },
    { title: 'Объём', get: t => money(t.volume, 2), sort: 'volume' },
    { title: 'Открыта', get: t => dt(t.open_time), numeric: false, sort: 'open_time' },
    { title: 'Закрыта', get: t => dt(t.close_time), numeric: false, sort: 'close_time' },
    { title: 'Вход', get: t => t.open_price },
    { title: 'Выход', get: t => t.close_price },
    { title: 'Стоп', get: t => (t.sl ? t.sl : '—') },
    { title: 'Риск', get: t => (t.risk_money ? money(t.risk_money, 0) : '—') },
    { title: 'R', get: t => (t.risk_money ? (t.net / t.risk_money).toFixed(2) : '—'), cls: t => signClass(t.net) },
    { title: 'Итог', get: t => signed(t.net), cls: t => signClass(t.net), sort: 'net' },
    { title: 'Длительность', get: t => duration(t.duration_minutes), sort: 'duration' },
  ];

  async function loadTrades(reset = true) {
    if (reset) state.tradesOffset = 0;
    const params = new URLSearchParams(cleanFilters());
    params.set('limit', '200');
    params.set('offset', String(state.tradesOffset));
    params.set('sort', state.tradesSort.key);
    params.set('desc', String(state.tradesSort.desc));
    const data = await api(`/api/trades?${params}`);

    const host = $('table-trades');
    if (reset) {
      table(host, TRADE_COLUMNS, data.rows);
      host.querySelectorAll('th[data-sort]').forEach(th => {
        th.addEventListener('click', () => {
          const key = th.dataset.sort;
          state.tradesSort = {
            key,
            desc: state.tradesSort.key === key ? !state.tradesSort.desc : true,
          };
          loadTrades(true);
        });
      });
    } else {
      const body = host.querySelector('tbody');
      body.insertAdjacentHTML('beforeend', data.rows.map(row =>
        '<tr>' + TRADE_COLUMNS.map(c => {
          const cls = [c.cls ? c.cls(row) : '', c.numeric === false ? '' : 'num'].filter(Boolean).join(' ');
          return `<td class="${cls}">${c.get(row)}</td>`;
        }).join('') + '</tr>').join(''));
    }

    state.tradesOffset += data.rows.length;
    $('trades-hint').textContent =
      `Показано ${state.tradesOffset} из ${data.total}. Клик по заголовку — сортировка.`;
    $('btn-more').hidden = state.tradesOffset >= data.total;
  }

  /* ---------- data loading ---------- */
  function cleanFilters() {
    const out = {};
    for (const [key, value] of Object.entries(state.filters)) if (value) out[key] = value;
    return out;
  }

  async function refresh() {
    const health = await api('/api/health');
    const hasData = health.trades > 0;
    $('empty').hidden = hasData;
    $('filters').hidden = !hasData;
    $('tabs').hidden = !hasData;
    if (!hasData) {
      document.querySelectorAll('.panel').forEach(p => (p.hidden = true));
      return;
    }
    showActivePanel();

    const options = await api('/api/filters');
    fillSelect($('f-symbol'), options.symbols, 'Все');
    fillSelect($('f-magic'), options.magics.map(String), 'Все');
    if (!$('f-from').value && options.date_from) {
      $('f-from').min = options.date_from;
      $('f-to').max = options.date_to;
    }

    const data = await api(`/api/dashboard?${new URLSearchParams(cleanFilters())}`);
    state.data = data;
    state.currency = data.currency || '';

    if (data.account) {
      $('acct-strip').hidden = false;
      $('acct-login').textContent = data.account.login || '—';
      $('acct-server').textContent = [data.account.name, data.account.server].filter(Boolean).join(' · ')
        || 'статистика счёта MetaTrader';
      $('acct-balance').textContent = `${money(data.account.balance, 2)} ${state.currency}`;
      $('acct-equity').textContent = data.account.equity
        ? `${money(data.account.equity, 2)} ${state.currency}` : '—';
      $('acct-open').textContent = data.open_positions.length;
    }

    renderOverview(data);
    renderSymbols(data);
    renderTime(data);
    renderRisk(data);
    await loadTrades(true);
  }

  /** The panels start hidden in the markup, so the first load has to reveal one. */
  function showActivePanel() {
    const active = $('tabs').querySelector('.tab[aria-selected="true"]');
    const name = active ? active.dataset.panel : 'overview';
    document.querySelectorAll('.panel').forEach(p => (p.hidden = p.id !== `panel-${name}`));
  }

  function fillSelect(select, values, allLabel) {
    const current = select.value;
    select.innerHTML = `<option value="">${allLabel}</option>`
      + values.map(v => `<option value="${v}">${v}</option>`).join('');
    if (values.includes(current)) select.value = current;
  }

  /* ---------- events ---------- */
  function bindFilters() {
    const apply = () => {
      state.filters.from = $('f-from').value;
      state.filters.to = $('f-to').value;
      state.filters.symbol = $('f-symbol').value;
      state.filters.magic = $('f-magic').value;
      refresh().catch(showError);
    };
    ['f-from', 'f-to', 'f-symbol', 'f-magic'].forEach(id => $(id).addEventListener('change', apply));

    $('quick-ranges').addEventListener('click', event => {
      const button = event.target.closest('.chip');
      if (!button) return;
      $('quick-ranges').querySelectorAll('.chip')
        .forEach(c => c.setAttribute('aria-pressed', String(c === button)));
      const days = Number(button.dataset.days);
      if (!days) {
        $('f-from').value = '';
        $('f-to').value = '';
      } else {
        const to = new Date();
        const from = new Date(Date.now() - days * 86400000);
        $('f-from').value = from.toISOString().slice(0, 10);
        $('f-to').value = to.toISOString().slice(0, 10);
      }
      apply();
    });

    $('btn-export').addEventListener('click', () => {
      const params = new URLSearchParams(cleanFilters());
      if (token) params.set('token', token);
      window.open(`/api/export/trades.csv?${params}`, '_blank');
    });
  }

  function bindTabs() {
    $('tabs').addEventListener('click', event => {
      const tab = event.target.closest('.tab');
      if (!tab) return;
      $('tabs').querySelectorAll('.tab')
        .forEach(t => t.setAttribute('aria-selected', String(t === tab)));
      document.querySelectorAll('.panel')
        .forEach(p => (p.hidden = p.id !== `panel-${tab.dataset.panel}`));
      Charts.hideTip();
      window.dispatchEvent(new Event('resize'));
    });
  }

  function status(kind, text) {
    const node = $('import-status');
    node.dataset.kind = kind;
    node.innerHTML = text;
  }

  function showError(error) {
    status('err', error.message || String(error));
    $('dlg-import').showModal();
  }

  function bindImport() {
    const dialog = $('dlg-import');
    const open = () => { status('', ''); $('import-status').removeAttribute('data-kind'); dialog.showModal(); };
    $('btn-import').addEventListener('click', open);
    $('btn-import-empty').addEventListener('click', open);
    $('dlg-close').addEventListener('click', () => dialog.close());

    const drop = $('drop');
    const input = $('file');
    drop.addEventListener('click', () => input.click());
    input.addEventListener('change', () => input.files[0] && upload(input.files[0]));
    ['dragenter', 'dragover'].forEach(name => drop.addEventListener(name, event => {
      event.preventDefault(); drop.classList.add('over');
    }));
    ['dragleave', 'drop'].forEach(name => drop.addEventListener(name, event => {
      event.preventDefault(); drop.classList.remove('over');
    }));
    drop.addEventListener('drop', event => {
      const file = event.dataTransfer.files[0];
      if (file) upload(file);
    });

    $('btn-sync').addEventListener('click', async () => {
      const button = $('btn-sync');
      button.disabled = true;
      const label = button.textContent;
      button.innerHTML = '<span class="spinner"></span> Синхронизация…';
      try {
        const result = await api('/api/sync/mt5', { method: 'POST' });
        status('ok', `Синхронизировано с MT5: добавлено ${result.trades_added}, обновлено ${result.trades_updated}.`);
        dialog.showModal();
        await refresh();
      } catch (error) {
        showError(error);
      } finally {
        button.disabled = false;
        button.textContent = label;
      }
    });
  }

  async function upload(file) {
    status('info', `<span class="spinner"></span> Читаю ${file.name}…`);
    const form = new FormData();
    form.append('file', file);
    try {
      const result = await api('/api/import/file', { method: 'POST', body: form });
      const warnings = (result.warnings || []).map(w => `<br><span style="color:var(--muted)">${w}</span>`).join('');
      status('ok', `Готово: найдено ${result.trades_seen} сделок, добавлено ${result.trades_added}, `
        + `обновлено ${result.trades_updated}.${warnings}`);
      await refresh();
    } catch (error) {
      status('err', error.message);
    }
  }

  function bindTheme() {
    const stored = localStorage.getItem('tradingapp_theme');
    if (stored) document.documentElement.dataset.theme = stored;
    $('btn-theme').addEventListener('click', () => {
      const root = document.documentElement;
      const dark = root.dataset.theme
        ? root.dataset.theme === 'dark'
        : window.matchMedia('(prefers-color-scheme: dark)').matches;
      root.dataset.theme = dark ? 'light' : 'dark';
      localStorage.setItem('tradingapp_theme', root.dataset.theme);
      if (state.data) {
        renderOverview(state.data);
        renderSymbols(state.data);
        renderTime(state.data);
        renderRisk(state.data);
      }
    });
  }

  bindFilters();
  bindTabs();
  bindImport();
  bindTheme();
  $('btn-more').addEventListener('click', () => loadTrades(false).catch(showError));
  refresh().catch(error => {
    document.querySelector('#empty p').textContent = `Не удалось связаться с сервером: ${error.message}`;
  });
})();
