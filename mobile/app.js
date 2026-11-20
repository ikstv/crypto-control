"use strict";
const element = (id) => document.getElementById(id);
const date = new Intl.DateTimeFormat("uk-UA", {dateStyle: "short", timeStyle: "short"});
const refreshDelay = 15000;

function show(data) {
  const state = {ok: "WhiteBIT підключено", partial: "WhiteBIT · часткові дані", error: "WhiteBIT недоступний"};
  const stale = Date.now() - Date.parse(data.updated_at) > 180000;
  element("connection").textContent = stale ? "Дані WhiteBIT застаріли" : state[data.whitebit.status];
  const checkedAt = date.format(new Date(data.updated_at));
  const successAt = data.last_success_at ? date.format(new Date(data.last_success_at)) : "ще не було";
  element("updated").textContent = "Остання перевірка " + checkedAt + " · Успішне оновлення " + successAt;

  for (const asset of ["DBTC", "DUSDT"]) {
    for (const field of ["available", "freeze"]) {
      element(asset + "-" + field).textContent = data.whitebit.balances[asset]?.[field] ?? "—";
    }
  }

  const engine = {
    active: "Працює", inactive: "Зупинено", failed: "Помилка служби",
    not_running: "Процес не запущено", process_present: "Процес запущено",
  };
  const startup = {enabled: "автозапуск увімкнено", disabled: "автозапуск вимкнено"};
  const suffix = startup[data.engine.startup];
  element("engine").textContent = (engine[data.engine.active] ?? "Стан невідомий") + (suffix ? " · " + suffix : "");

  const log = data.decisions;
  const shadow = data.shadow;
  const current = shadow && Date.now() / 1000 - shadow.observed < 660;
  element("shadow-state").textContent = !shadow ? "Спостереження недоступне" :
    (current ? "Спостереження оновлюється" : "Спостереження застаріло") +
    " · Пропущено інтервалів: " + shadow.missing;
  element("shadow-model").textContent = shadow ?
    "Умовна позиція " + shadow.position_btc + " BTC · Немає угод, витрат або оцінки прибутковості" : "";
  element("shadow-log").replaceChildren(...(shadow?.entries ?? []).map((row) => {
    const item = document.createElement("li");
    const decision = row.reason === "no_candidate" ? "Утримання · немає допущеної стратегії" :
      "Пропуск · свіжий стакан недоступний";
    item.textContent = date.format(new Date(row.observed * 1000)) + " · " +
      (row.mid ? row.mid + " USDT (mid)" : "ціна недоступна") + " · " + decision;
    return item;
  }));
  element("decision-state").textContent = {
    not_initialized: "Журнал ще не створено.",
    unavailable: "Сервер не має доступу до журналу.",
    ok: log.entries.length ? "" : "Записаних рішень поки немає.",
  }[log.status];
  element("decisions").replaceChildren(...log.entries.map((row) => {
    const item = document.createElement("li");
    item.textContent = date.format(new Date(row.time)) + " · " + row.decision;
    return item;
  }));
}

async function refresh() {
  try {
    const response = await fetch("/api/status", {
      cache: "no-store", signal: AbortSignal.timeout(10000),
    });
    if (response.status === 503) {
      element("connection").textContent = "Очікуємо першу перевірку…";
    } else {
      if (!response.ok) throw new Error("status unavailable");
      show(await response.json());
    }
  } catch {
    element("connection").textContent = "Панель недоступна · останні показані дані можуть бути застарілі";
  } finally {
    setTimeout(refresh, refreshDelay);
  }
}

if ("serviceWorker" in navigator && window.isSecureContext) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}
refresh();
