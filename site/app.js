/* GCC Bank AI Tracker – static dashboard (no build step). */
(() => {
  "use strict";

  const app = document.getElementById("app");

  // Brand
  const BRAND = { name: "GCC Banking", word: "AI Pulse Monitor", tagline: "Tracking AI in Gulf banks, daily" };
  document.title = `${BRAND.name} – ${BRAND.word}`;

  const ICONS = {
    news: '<path d="M5 5h11v14H6a1 1 0 0 1-1-1V5zM16 9h3v9a1 1 0 0 1-1 1h-2M8 9h5M8 12h5M8 15h3"/>',
    bank: '<path d="M3 10l9-6 9 6M5 10v8M9.5 10v8M14.5 10v8M19 10v8M3 20h18"/>',
    globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
    bolt: '<path d="M13 3 5 13h6l-1 8 8-10h-6l1-8z"/>',
  };
  const icon = (k) => `<svg viewBox="0 0 24 24" aria-hidden="true">${ICONS[k]}</svg>`;
  // Decorative AI-network pattern for the hero.
  const NETWORK = (() => {
    const pts = [[30,40],[90,20],[150,55],[210,25],[260,70],[60,100],[130,115],[200,105],[250,140],[95,165],[175,170],[235,200],[40,190]];
    const edges = [[0,1],[1,2],[2,3],[3,4],[0,5],[5,6],[6,2],[6,7],[7,4],[7,8],[5,9],[9,6],[9,10],[10,7],[10,11],[8,11],[12,9],[1,6],[2,7]];
    return `<svg class="hero-net" viewBox="0 0 280 220" aria-hidden="true">${edges.map(([a, b]) =>
      `<line x1="${pts[a][0]}" y1="${pts[a][1]}" x2="${pts[b][0]}" y2="${pts[b][1]}"/>`).join("")}${pts.map(([x, y], i) =>
      `<circle cx="${x}" cy="${y}" r="${i % 3 ? 3 : 5}"/>`).join("")}</svg>`;
  })();
  const PAGE = 25;
  const PERIODS = [
    ["all", "All time"], ["30d", "Last 30 days"], ["6m", "Last 6 months"], ["12m", "Last 12 months"], ["24m", "Last 24 months"],
  ];
  const TYPE_ORDER = { conventional: 0, islamic: 1, digital: 2, development: 3, central: 4 };

  let DATA = { items: [], updated_at: null };
  let CFG = { countries: [], banks: [], types: {} };
  let BANKS = {}, COUNTRIES = {}, CATEGORIES = [], LOGOS = {};

  // ---------- utils ----------
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const $ = (sel, root = document) => root.querySelector(sel);
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const fmtDate = (iso) => { const [y, m, d] = iso.split("-"); return `${+d} ${MONTHS[+m - 1]} ${y}`; };
  const initials = (b) => (b.short.replace(/[^A-Za-z0-9 ]/g, "").split(/\s+/).filter(Boolean).length > 1
    ? b.short.split(/\s+/).map((w) => w[0]).join("").slice(0, 3) : b.short.slice(0, 4)).toUpperCase();
  const plural = (n, w) => `${n.toLocaleString()} ${w}${n === 1 ? "" : "s"}`;
  const isoDaysAgo = (d) => new Date(Date.now() - d * 864e5).toISOString().slice(0, 10);
  const periodStart = (p) => ({ "30d": isoDaysAgo(30), "6m": isoDaysAgo(183), "12m": isoDaysAgo(365), "24m": isoDaysAgo(731) }[p] || "");

  function toast(msg) {
    const t = $("#toast"); t.textContent = msg; t.classList.add("show");
    clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove("show"), 1800);
  }

  // ---------- routing ----------
  function parseHash() {
    const raw = location.hash.replace(/^#\/?/, "");
    const [path, qs] = raw.split("?");
    return { parts: path.split("/").filter(Boolean), q: new URLSearchParams(qs || "") };
  }
  function setQuery(q) {
    const { parts } = parseHash();
    const s = q.toString();
    history.replaceState(null, "", `#/${parts.join("/")}${s ? "?" + s : ""}`);
  }
  function setActiveTab(tab) {
    document.querySelectorAll(".tabbar a").forEach((a) => a.classList.toggle("active", a.dataset.tab === tab));
  }

  function route() {
    const { parts, q } = parseHash();
    window.scrollTo(0, 0);
    const [view, arg] = parts;
    if (view === "countries") return setActiveTab("countries"), renderCountries();
    if (view === "country" && COUNTRIES[arg]) return setActiveTab("countries"), renderCountry(arg);
    if (view === "banks") return setActiveTab("banks"), renderBanks(q);
    if (view === "bank" && BANKS[arg]) return setActiveTab("banks"), renderBank(arg, q);
    if (view === "about") return setActiveTab("about"), renderAbout();
    setActiveTab("feed");
    renderFeed(q);
  }

  // ---------- shared components ----------
  // Themes in a fixed order (multi-label: an item can have several).
  const THEMES = ["AI Strategy & Leadership", "AI Adoption in Operations", "Generative AI & Copilots", "Tech Vendor Partnerships",
    "Customer-Facing AI", "Fraud, Risk & Compliance", "AI Skills & Talent", "Awards & Rankings", "AI Regulation & Policy"];
  const topicsOf = (i) => (i.topics && i.topics.length ? i.topics : [i.category]);
  const hasTopic = (i, t) => topicsOf(i).includes(t);
  const countBy = (items, keysOf) => {
    const m = new Map();
    items.forEach((i) => new Set(keysOf(i)).forEach((k) => k && m.set(k, (m.get(k) || 0) + 1)));
    return [...m.entries()].sort((a, z) => z[1] - a[1] || a[0].localeCompare(z[0]));
  };
  // Horizontal bars (one hue = magnitude); each row is a button that filters.
  function barList(id, rows, active, attr, total) {
    if (!rows.length) return `<p class="muted">Nothing yet.</p>`;
    const max = Math.max(...rows.map(([, n]) => n));
    return `<div class="cat-bars" id="${id}">${rows.map(([k, n]) =>
      `<button class="cat-bar" data-${attr}="${esc(k)}" aria-pressed="${k === active}"><span class="lbl">${esc(k)}</span>
        <span class="cnt">${n}${total ? ` <span class="pct">· ${Math.round((n / total) * 100)}%</span>` : ""}</span>
        <span class="track"><span class="fill" style="display:block;width:${(n / max) * 100}%"></span></span></button>`).join("")}</div>`;
  }
  // Group product/unit names under their vendor ("Microsoft 365 Copilot" → Microsoft) for the partners chart.
  const VENDORS = ["Microsoft", "Accenture", "Oracle", "Infosys", "Google", "Amazon", "AWS", "IBM", "SAP", "Salesforce", "Visa",
    "Mastercard", "G42", "Presight", "Core42", "HCLTech", "Intellect", "Temenos", "NVIDIA", "OpenAI", "Anthropic", "Huawei",
    "Capgemini", "Deloitte", "PwC", "EY", "KPMG", "McKinsey", "Bain", "BCG", "Boston Consulting Group", "Wipro", "TCS", "Cognizant"];
  const vendorOf = (p) => {
    const v = VENDORS.find((n) => p.toLowerCase() === n.toLowerCase() || p.toLowerCase().startsWith(n.toLowerCase() + " "));
    return v === "Amazon" ? "AWS" : v === "Boston Consulting Group" ? "BCG" : v || p;
  };
  const partnersOf = (i) => (i.partners || []).map(vendorOf);
  const VERIFY_LABEL = { official: "Official bank release", trusted: "Trusted outlet", corroborated: "Confirmed by several outlets" };
  function sourcesOf(i) {
    if (i.sources && i.sources.length) return i.sources;
    return [{ url: i.source_url, name: i.source_name }, ...(i.other_sources || []).map((u) => (typeof u === "string" ? { url: u, name: "" } : u))];
  }

  // Country flag as an image (emoji flags don't render on Windows).
  const flag = (c, cls = "") => `<img class="flag-img ${cls}" src="flags/${c.code.toLowerCase()}.svg" alt="${esc(c.name)} flag" title="${esc(c.name)}">`;

  // Bank logo (downloaded by the tracker into logos/) with the initials badge as fallback.
  function avatar(b, cls = "avatar") {
    const file = LOGOS[b.id] && LOGOS[b.id].file;
    const img = file ? `<img src="${esc(file)}" alt="" loading="lazy" onerror="this.parentNode.classList.remove('has-logo');this.remove()">` : "";
    return `<span class="${cls}${file ? " has-logo" : ""}" aria-hidden="true">${img}<span class="ini">${esc(initials(b))}</span></span>`;
  }

  function newsCard(i, { showBank = true } = {}) {
    const b = BANKS[i.bank_id], c = COUNTRIES[i.country];
    const tags = (i.tags || []).map((t) => `<span class="tag">${esc(t)}</span>`).join("");
    const extraTopics = topicsOf(i).filter((t) => t !== i.category).map((t) => `<span class="cat sub">${esc(t)}</span>`).join("");
    const srcs = sourcesOf(i);
    const v = i.verification && VERIFY_LABEL[i.verification.level];
    const outlets = new Set(srcs.map((s) => (s.name || "").toLowerCase()).filter(Boolean)).size;
    const badge = v ? `<div class="verified" title="Passed the bank-name, numbers and AI fact-checks">✓ ${esc(i.verification.level === "corroborated" ? `Confirmed by ${outlets} outlets` : v)}${i.verification.evidence === "article" ? " · checked against full article" : " · checked against headline"}</div>` : "";
    const orig = i.source_title && i.source_title.trim() !== i.title.trim()
      ? `<div class="orig"><span>Original headline:</span> <span dir="auto">${esc(i.source_title)}</span></div>` : "";
    const label = (s, n) => {
      if (s.name) return s.name;
      try { const h = new URL(s.url).hostname.replace(/^www\./, ""); return h === "news.google.com" ? `News source ${n + 1}` : h; }
      catch (_) { return `Source ${n + 1}`; }
    };
    const srcLinks = srcs.map((s, n) => `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(label(s, n))} ↗</a>`).join("");
    return `<article class="card news-card">
      <div class="meta">
        ${showBank ? `<a class="bank-chip" href="#/bank/${b.id}">${avatar(b, "avatar sm")}<span class="bank">${esc(b.short)}</span></a>${flag(c)}<span class="dot"></span>` : ""}
        <time datetime="${i.date}">${fmtDate(i.date)}</time>
        <span class="dot"></span><span class="cat">${esc(i.category)}</span>${extraTopics}
      </div>
      <h3><a href="${esc(i.source_url)}" target="_blank" rel="noopener">${esc(i.title)}</a></h3>
      ${i.summary ? `<p>${esc(i.summary)}</p>` : ""}
      ${i.impact ? `<div class="impact">📈 ${esc(i.impact)}</div>` : ""}
      ${tags || (i.partners && i.partners.length) ? `<div class="tags">${tags}${i.partners && i.partners.length ? `<span class="tag">🤝 ${esc(i.partners.join(", "))}</span>` : ""}</div>` : ""}
      <div class="sources">${badge}${orig}<div class="src-list"><span>${srcs.length > 1 ? "Sources" : "Source"}:</span>${srcLinks}</div></div>
    </article>`;
  }

  function stats(tiles) {
    return `<div class="stats">${tiles.map(([v, l]) => `<div class="stat"><div class="v">${v}</div><div class="l">${esc(l)}</div></div>`).join("")}</div>`;
  }

  function emptyState(msg) {
    const noData = !DATA.items.length;
    return `<div class="empty card"><div class="big">${noData ? "⏳" : "🔍"}</div>
      <p><b>${noData ? "History is being collected" : "No matching news"}</b></p>
      <p>${noData ? "The AI agent is loading the last 2 years of bank AI announcements. Check back shortly." : esc(msg || "Try widening the filters.")}</p></div>`;
  }

  // Monthly activity chart (single series → one hue, no legend; title names it).
  function chartBlock(id, title) {
    return `<div class="card chart-card"><div class="chart-head"><b>${esc(title)}</b><span>Announcements per month</span></div>
      <div class="chart" id="${id}"></div></div>`;
  }
  function mountChart(id, items, months = 24) {
    const el = document.getElementById(id);
    if (!el) return;
    const now = new Date();
    const buckets = [];
    for (let k = months - 1; k >= 0; k--) {
      const d = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - k, 1));
      buckets.push({ key: d.toISOString().slice(0, 7), label: `${MONTHS[d.getUTCMonth()]} ${String(d.getUTCFullYear()).slice(2)}`, n: 0 });
    }
    const idx = Object.fromEntries(buckets.map((b, i) => [b.key, i]));
    items.forEach((i) => { const k = idx[i.date.slice(0, 7)]; if (k !== undefined) buckets[k].n++; });

    const W = Math.max(280, el.clientWidth), H = 150, padL = 26, padB = 20, padT = 8;
    const max = Math.max(1, ...buckets.map((b) => b.n));
    const step = max <= 4 ? 1 : max <= 10 ? 2 : max <= 25 ? 5 : Math.ceil(max / 5 / 5) * 5;
    const top = Math.ceil(max / step) * step;
    const band = (W - padL) / buckets.length;
    const bw = Math.min(24, Math.max(3, band - 2));
    const y = (v) => padT + (H - padT - padB) * (1 - v / top);
    let svg = "";
    for (let v = 0; v <= top; v += step) {
      svg += `<line class="grid" x1="${padL}" x2="${W}" y1="${y(v)}" y2="${y(v)}"/><text x="${padL - 6}" y="${y(v) + 3.5}" text-anchor="end">${v}</text>`;
    }
    const labelEvery = band < 18 ? 6 : band < 34 ? 3 : 1;
    buckets.forEach((b, i) => {
      const x = padL + i * band + (band - bw) / 2;
      if (b.n) {
        const yt = y(b.n), h = y(0) - yt, r = Math.min(4, h, bw / 2);
        svg += `<rect class="bar-hit" data-i="${i}" x="${padL + i * band}" y="${padT}" width="${band}" height="${H - padT - padB}"/>`;
        svg += `<path class="bar" data-i="${i}" d="M${x},${y(0)}V${yt + r}Q${x},${yt} ${x + r},${yt}H${x + bw - r}Q${x + bw},${yt} ${x + bw},${yt + r}V${y(0)}Z"/>`;
      } else {
        svg += `<rect class="bar-hit" data-i="${i}" x="${padL + i * band}" y="${padT}" width="${band}" height="${H - padT - padB}"/>`;
      }
      if ((buckets.length - 1 - i) % labelEvery === 0) svg += `<text x="${padL + i * band + band / 2}" y="${H - 4}" text-anchor="middle">${b.label}</text>`;
    });
    el._redraw = () => mountChart(id, items, months);
    const wrap = document.createElement("div");
    wrap.style.position = "relative";
    wrap.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Monthly announcements, last ${months} months">${svg}</svg><div class="tooltip"></div>`;
    el.replaceChildren(wrap);
    const tip = wrap.querySelector(".tooltip");
    const show = (e) => {
      const t = e.target.closest("[data-i]"); if (!t) return;
      const b = buckets[+t.dataset.i];
      el.querySelectorAll(".bar").forEach((p) => p.classList.toggle("hl", p.dataset.i === t.dataset.i));
      tip.textContent = `${b.label.replace(" ", " 20")} · ${plural(b.n, "announcement")}`;
      const rect = wrap.getBoundingClientRect();
      tip.style.left = `${Math.min(Math.max(padL + (+t.dataset.i + 0.5) * band, 70), rect.width - 70)}px`;
      tip.style.top = `${Math.max(y(b.n), 30)}px`;
      tip.classList.add("show");
    };
    wrap.addEventListener("pointermove", show);
    wrap.addEventListener("pointerdown", show);
    wrap.addEventListener("pointerleave", () => { tip.classList.remove("show"); el.querySelectorAll(".bar").forEach((p) => p.classList.remove("hl")); });
  }

  // ---------- Feed ----------
  function renderFeed(q) {
    const st = {
      country: q.get("country") || "", bank: q.get("bank") || "", cat: q.get("cat") || "",
      period: q.get("period") || "all", q: q.get("q") || "",
    };
    const countryChips = [["", "All GCC", DATA.items.length]].concat(
      CFG.countries.map((c) => [c.code, `${flag(c)} ${esc(c.name)}`, DATA.items.filter((i) => i.country === c.code).length]));

    const maxC = Math.max(1, ...countryChips.slice(1).map(([, , n]) => n));
    const updated = DATA.updated_at ? new Date(DATA.updated_at).toLocaleString([], { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" }) : "";
    app.innerHTML = `
      <section class="hero">
        ${NETWORK}
        <div class="hero-eyebrow"><span class="live-dot"></span>Live${updated ? ` · updated ${esc(updated)}` : ""}</div>
        <h1 class="hero-title"><span class="hero-kicker">${esc(BRAND.name)}</span><b>${esc(BRAND.word)}</b></h1>
        <p class="hero-tag">${esc(BRAND.tagline)}</p>
        <div class="hero-kpis" id="heroKpis"></div>
        <div class="hero-countries" id="countryChips">
          <button class="hc" data-country="" aria-pressed="${!st.country}"><span class="hc-all">GCC</span><span class="hc-n">${DATA.items.length}</span><span class="hc-l">All</span></button>
          ${CFG.countries.map((c) => {
            const n = DATA.items.filter((i) => i.country === c.code).length;
            return `<button class="hc" data-country="${c.code}" aria-pressed="${st.country === c.code}">${flag(c, "hc-flag")}<span class="hc-n">${n}</span><span class="hc-l">${esc(c.name)}</span><span class="hc-bar"><span style="width:${(n / maxC) * 100}%"></span></span></button>`;
          }).join("")}
        </div>
      </section>
      <div class="filters">
        <label class="search"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/></svg>
          <input id="fq" type="search" placeholder="Search news, partners, tags…" value="${esc(st.q)}" aria-label="Search"></label>
        <select id="fbank" aria-label="Bank"></select>
        <select id="fcat" aria-label="Theme"><option value="">All themes</option>${CATEGORIES.map((c) => `<option ${c === st.cat ? "selected" : ""}>${esc(c)}</option>`).join("")}</select>
        <select id="fperiod" aria-label="Period">${PERIODS.map(([v, l]) => `<option value="${v}" ${v === st.period ? "selected" : ""}>${l}</option>`).join("")}</select>
      </div>
      <div style="margin-top:10px">${chartBlock("feedChart", "Activity")}</div>
      <div class="insights">
        <div class="card"><div class="chart-head"><b>AI themes</b><span>tap to filter</span></div><div id="themeBars"></div></div>
        <div class="card"><div class="chart-head"><b>Top tech partners</b><span>tap to filter</span></div><div id="partnerBars"></div></div>
      </div>
      <div class="result-line"><span id="resCount"></span><button class="linklike" id="clearBtn" hidden>Clear filters</button></div>
      <div class="news-list" id="feedList"></div>
      <button class="more-btn" id="moreBtn" hidden>Show more</button>
      <p class="updated">${DATA.updated_at ? "Updated " + new Date(DATA.updated_at).toLocaleString() : ""} · checked daily</p>`;

    const fillBanks = () => {
      const banks = CFG.banks.filter((b) => !st.country || b.country === st.country)
        .sort((a, b) => a.country.localeCompare(b.country) || a.short.localeCompare(b.short));
      if (st.bank && !banks.some((b) => b.id === st.bank)) st.bank = "";
      $("#fbank").innerHTML = `<option value="">All banks</option>` + banks.map((b) =>
        `<option value="${b.id}" ${b.id === st.bank ? "selected" : ""}>${esc(b.short)}${st.country ? "" : " · " + esc(COUNTRIES[b.country].name)}</option>`).join("");
    };

    let shown = PAGE;
    const update = (resetPage = true) => {
      if (resetPage) shown = PAGE;
      const qp = new URLSearchParams();
      Object.entries(st).forEach(([k, v]) => { if (v && !(k === "period" && v === "all")) qp.set(k, v); });
      setQuery(qp);
      const start = periodStart(st.period);
      const needle = st.q.trim().toLowerCase();
      const base = DATA.items.filter((i) =>
        (!st.country || i.country === st.country) && (!st.bank || i.bank_id === st.bank) && (!start || i.date >= start) &&
        (!needle || [i.title, i.source_title, i.summary, i.source_name, BANKS[i.bank_id].name, BANKS[i.bank_id].short,
          ...(i.tags || []), ...(i.partners || []), ...partnersOf(i), ...topicsOf(i)].join(" ").toLowerCase().includes(needle)));
      const list = base.filter((i) => !st.cat || hasTopic(i, st.cat));
      const banksActive = new Set(list.map((i) => i.bank_id)).size;
      const last30 = list.filter((i) => i.date >= isoDaysAgo(30)).length;
      const countries = new Set(list.map((i) => i.country)).size;
      $("#heroKpis").innerHTML = [["news", list.length.toLocaleString(), "AI announcements"], ["bank", banksActive, "banks active"],
        ["globe", countries, "countries"], ["bolt", last30, "in the last 30 days"]]
        .map(([k, v, l]) => `<div class="kpi">${icon(k)}<b>${v}</b><span>${l}</span></div>`).join("");
      $("#resCount").textContent = list.length ? `Showing ${Math.min(shown, list.length)} of ${plural(list.length, "item")}` : "";
      $("#clearBtn").hidden = !(st.country || st.bank || st.cat || st.q || st.period !== "all");
      $("#feedList").innerHTML = list.length ? list.slice(0, shown).map((i) => newsCard(i)).join("") : emptyState();
      $("#moreBtn").hidden = list.length <= shown;
      $("#moreBtn").onclick = () => { shown += PAGE; update(false); };
      mountChart("feedChart", list);
      // Themes are counted on everything except the theme filter itself, so all bars stay comparable.
      $("#themeBars").innerHTML = barList("themeList", countBy(base, topicsOf), st.cat, "theme", base.length);
      $("#partnerBars").innerHTML = barList("partnerList", countBy(list, partnersOf).slice(0, 8), st.q, "partner");
    };
    $("#themeBars").addEventListener("click", (e) => {
      const x = e.target.closest("[data-theme]"); if (!x) return;
      st.cat = x.dataset.theme === st.cat ? "" : x.dataset.theme; $("#fcat").value = st.cat; update();
    });
    $("#partnerBars").addEventListener("click", (e) => {
      const x = e.target.closest("[data-partner]"); if (!x) return;
      st.q = x.dataset.partner === st.q ? "" : x.dataset.partner; $("#fq").value = st.q; update();
    });

    $("#countryChips").addEventListener("click", (e) => {
      const btn = e.target.closest("[data-country]"); if (!btn) return;
      st.country = btn.dataset.country;
      document.querySelectorAll("#countryChips [data-country]").forEach((c) => c.setAttribute("aria-pressed", c === btn));
      fillBanks(); update();
    });
    $("#fbank").onchange = (e) => { st.bank = e.target.value; update(); };
    $("#fcat").onchange = (e) => { st.cat = e.target.value; update(); };
    $("#fperiod").onchange = (e) => { st.period = e.target.value; update(); };
    let t; $("#fq").oninput = (e) => { clearTimeout(t); t = setTimeout(() => { st.q = e.target.value; update(); }, 180); };
    $("#clearBtn").onclick = () => { location.hash = "#/"; };
    fillBanks(); update();

  }

  // ---------- Countries ----------
  function renderCountries() {
    const cards = CFG.countries.map((c) => {
      const items = DATA.items.filter((i) => i.country === c.code);
      const banks = CFG.banks.filter((b) => b.country === c.code);
      const active = new Set(items.map((i) => i.bank_id)).size;
      const latest = items[0];
      return `<a class="card country-card" href="#/country/${c.code}">
        <div class="row">${flag(c, "lg")}<div><h2>${esc(c.name)}</h2>
          <div class="nums">${plural(items.length, "announcement")} · ${active}/${banks.length} banks active</div></div><span class="chev">›</span></div>
        ${latest ? `<div class="latest">Latest: <b>${esc(BANKS[latest.bank_id].short)}</b> — ${esc(latest.title)} <span style="color:var(--text-3)">(${fmtDate(latest.date)})</span></div>` : ""}
      </a>`;
    }).join("");
    app.innerHTML = `<div class="page-head"><h1>Countries</h1><div class="sub">Tap a country to see its banks and AI activity</div></div>
      <div class="grid-2" style="margin-top:12px">${cards}</div>`;
  }

  function bankRows(banks, counts) {
    const max = Math.max(1, ...banks.map((b) => counts[b.id] || 0));
    return `<div class="bank-list">${banks.map((b) => {
      const n = counts[b.id] || 0;
      return `<a class="bank-row" href="#/bank/${b.id}">${avatar(b)}
        <div class="info"><div class="name">${esc(b.name)}</div><div class="sub">${esc(CFG.types[b.type] || b.type)}</div>
        <div class="bar" style="width:${(n / max) * 100}%;${n ? "" : "opacity:0"}"></div></div>
        <span class="count">${n}</span><span class="chev">›</span></a>`;
    }).join("")}</div>`;
  }
  const countsByBank = (items) => items.reduce((m, i) => ((m[i.bank_id] = (m[i.bank_id] || 0) + 1), m), {});

  function renderCountry(code) {
    const c = COUNTRIES[code];
    const items = DATA.items.filter((i) => i.country === code);
    const counts = countsByBank(items);
    const banks = CFG.banks.filter((b) => b.country === code)
      .sort((a, b) => (counts[b.id] || 0) - (counts[a.id] || 0) || TYPE_ORDER[a.type] - TYPE_ORDER[b.type]);
    const y1 = items.filter((i) => i.date >= isoDaysAgo(365)).length;
    app.innerHTML = `<a class="back" href="#/countries">‹ Countries</a>
      <div class="page-head"><h1>${flag(c, "md")} ${esc(c.name)}</h1><div class="sub">AI activity of ${banks.length} tracked banks</div></div>
      ${stats([[items.length, "Announcements"], [Object.keys(counts).length, "Banks active"], [y1, "Last 12 months"]])}
      <div style="margin-top:10px">${chartBlock("cChart", `${c.name} activity`)}</div>
      <div class="insights">
        <div class="card"><div class="chart-head"><b>AI themes in ${esc(c.name)}</b><span>tap to see the news</span></div>${barList("cThemes", countBy(items, topicsOf), "", "theme", items.length)}</div>
        <div class="card"><div class="chart-head"><b>Top tech partners</b><span>tap to see the news</span></div>${barList("cPartners", countBy(items, partnersOf).slice(0, 8), "", "partner")}</div>
      </div>
      <div class="section-title">Banks by AI activity</div>${bankRows(banks, counts)}
      <div class="section-title">Latest news</div>
      <div class="news-list">${items.slice(0, 8).map((i) => newsCard(i)).join("") || emptyState()}</div>
      ${items.length > 8 ? `<a class="more-btn" style="display:grid;place-items:center" href="#/?country=${code}">See all ${items.length} news</a>` : ""}`;
    mountChart("cChart", items);
    app.querySelectorAll("#cThemes [data-theme]").forEach((x) => x.onclick = () => { location.hash = `#/?country=${code}&cat=${encodeURIComponent(x.dataset.theme)}`; });
    app.querySelectorAll("#cPartners [data-partner]").forEach((x) => x.onclick = () => { location.hash = `#/?country=${code}&q=${encodeURIComponent(x.dataset.partner)}`; });
  }

  // ---------- Banks ----------
  function renderBanks(q) {
    let country = q.get("country") || "";
    let needle = "";
    const counts = countsByBank(DATA.items);
    app.innerHTML = `<div class="page-head"><h1>Banks</h1><div class="sub">${CFG.banks.length} banks, Islamic banks, digital banks and central banks</div></div>
      <div class="chips" id="bChips">${[["", "All"]].concat(CFG.countries.map((c) => [c.code, `${flag(c)} ${esc(c.name)}`]))
        .map(([v, l]) => `<button class="chip" data-country="${v}" aria-pressed="${v === country}">${l}</button>`).join("")}</div>
      <div class="filters"><label class="search"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/></svg>
        <input id="bq" type="search" placeholder="Find a bank…" aria-label="Find a bank"></label></div>
      <div id="bankGroups"></div>`;
    const draw = () => {
      const groups = CFG.countries.filter((c) => !country || c.code === country).map((c) => {
        const banks = CFG.banks.filter((b) => b.country === c.code &&
          (!needle || `${b.name} ${b.short} ${b.name_ar || ""} ${(b.aliases || []).join(" ")}`.toLowerCase().includes(needle)))
          .sort((a, b) => (counts[b.id] || 0) - (counts[a.id] || 0) || a.short.localeCompare(b.short));
        return banks.length ? `<div class="section-title">${flag(c)} ${esc(c.name)}</div>${bankRows(banks, counts)}` : "";
      }).join("");
      $("#bankGroups").innerHTML = groups || `<div class="empty">No bank matches “${esc(needle)}”.</div>`;
    };
    $("#bChips").addEventListener("click", (e) => {
      const btn = e.target.closest("[data-country]"); if (!btn) return;
      country = btn.dataset.country;
      document.querySelectorAll("#bChips .chip").forEach((c) => c.setAttribute("aria-pressed", c === btn));
      setQuery(new URLSearchParams(country ? { country } : {})); draw();
    });
    $("#bq").oninput = (e) => { needle = e.target.value.trim().toLowerCase(); draw(); };
    draw();
  }

  function renderBank(id, q) {
    const b = BANKS[id], c = COUNTRIES[b.country];
    const all = DATA.items.filter((i) => i.bank_id === id);
    let cat = q.get("cat") || "";
    const cats = countBy(all, topicsOf);
    const maxCat = Math.max(1, ...cats.map(([, n]) => n));
    const first = all.length ? all[all.length - 1].date : null;
    const partners = [...new Set(all.flatMap((i) => i.partners || []))];

    app.innerHTML = `<a class="back" href="#/country/${b.country}">‹ ${flag(c)} ${esc(c.name)}</a>
      <div class="profile">${avatar(b)}<div>
        <h1>${esc(b.name)}</h1>
        <div class="sub">${flag(c)} ${esc(c.name)} · ${esc(CFG.types[b.type] || b.type)}${b.name_ar ? ` · <span class="ar">${esc(b.name_ar)}</span>` : ""}</div>
        <div class="sub"><a href="https://${esc(b.domain)}" target="_blank" rel="noopener">${esc(b.domain)} ↗</a></div></div></div>
      ${stats([[all.length, "AI announcements"], [all.filter((i) => i.date >= isoDaysAgo(365)).length, "Last 12 months"], [first ? first.slice(0, 4) : "–", "Tracked since"]])}
      <div style="margin-top:10px">${chartBlock("bChart", `${b.short} activity`)}</div>
      ${cats.length ? `<div class="section-title">Focus areas</div><div class="card"><div class="cat-bars" id="catBars">${cats.map(([k, n]) =>
        `<button class="cat-bar" data-cat="${esc(k)}" aria-pressed="${k === cat}"><span class="lbl">${esc(k)}</span><span class="cnt">${n}</span>
          <span class="track"><span class="fill" style="display:block;width:${(n / maxCat) * 100}%"></span></span></button>`).join("")}</div></div>` : ""}
      ${partners.length ? `<div class="section-title">Technology partners</div><div class="tags">${partners.map((p) => `<span class="tag">${esc(p)}</span>`).join("")}</div>` : ""}
      <div class="section-title" id="tlTitle">AI timeline</div><div id="timeline"></div>`;

    const drawTimeline = () => {
      const items = all.filter((i) => !cat || hasTopic(i, cat));
      $("#tlTitle").innerHTML = `AI timeline${cat ? ` · ${esc(cat)} <button class="linklike" id="clrCat">(show all)</button>` : ""}`;
      if (!items.length) { $("#timeline").innerHTML = emptyState(`No AI announcements tracked for ${b.short} yet.`); return; }
      let html = "", year = "";
      items.forEach((i) => {
        if (i.date.slice(0, 4) !== year) { year = i.date.slice(0, 4); html += `<div class="year-head">${year}</div>`; }
        html += newsCard(i, { showBank: false });
      });
      $("#timeline").innerHTML = `<div class="timeline">${html}</div>`;
      const clr = $("#clrCat"); if (clr) clr.onclick = () => setCat("");
    };
    const setCat = (v) => {
      cat = v; setQuery(new URLSearchParams(cat ? { cat } : {}));
      document.querySelectorAll("#catBars .cat-bar").forEach((x) => x.setAttribute("aria-pressed", x.dataset.cat === cat));
      drawTimeline();
    };
    const bars = $("#catBars");
    if (bars) bars.addEventListener("click", (e) => { const x = e.target.closest("[data-cat]"); if (x) setCat(x.dataset.cat === cat ? "" : x.dataset.cat); });
    drawTimeline();
    mountChart("bChart", all);
  }

  // ---------- About ----------
  function renderAbout() {
    app.innerHTML = `<div class="page-head"><h1>About</h1></div>
      <div class="card prose">
        <p>This tracker follows the <b>AI initiatives of ${CFG.banks.length} banks</b> across the UAE, Saudi Arabia, Qatar, Kuwait,
        Oman and Bahrain — conventional, Islamic and digital banks plus the six central banks.</p>
        <h2>What's included</h2>
        <ul><li>AI strategy, investment and programmes announced by a bank</li><li>GenAI, assistants, ML models and automation in production</li>
        <li>AI for fraud, AML, risk and compliance</li><li>AI partnerships where the bank is a party, plus AI talent programmes and awards</li>
        <li>Measurable AI outcomes, and central-bank AI regulation</li></ul>
        <h2>What's excluded</h2>
        <p>General AI or tech news, fintech or startup news with no bank involved, and generic "digital transformation" with no AI component.</p>
        <h2>How it works</h2>
        <p><b>Once a day</b>, the tracker reads the banks' own newsrooms and scans English and Arabic news for every bank. An AI model screens each headline, keeps only
        qualifying items, summarises and categorises them, and sends new items to Telegram. Every item links to its original source.
        The history goes back 24 months.</p>
        <h2>How every item is verified</h2>
        <ul>
          <li><b>Real source:</b> the link always comes from a news feed or the bank's own newsroom, never from the AI.</li>
          <li><b>Bank named:</b> the original headline or article must name the bank (English or Arabic).</li>
          <li><b>No invented numbers:</b> every figure shown must appear in the source.</li>
          <li><b>Independent AI fact-check:</b> a second AI pass checks each claim against the full article (or the original headline); unsupported details are removed, and wrong-bank or non-AI items are rejected.</li>
          <li><b>Trusted source:</b> the bank's own newsroom, an official news agency or a reputable outlet; anything else is published only after a second outlet confirms it.</li>
        </ul>
        <p>Each card shows the result (e.g. "✓ Trusted outlet · checked against full article"), the original headline and every source.</p>
        <h2>Themes</h2>
        <p>${THEMES.map(esc).join(" · ")}. A story can belong to more than one theme.</p>
        <p style="font-size:13px">Summaries are AI-generated and fact-checked, but please open the source before citing a figure.</p>
      </div>
      <p class="updated">${plural(DATA.items.length, "announcement")} tracked${DATA.updated_at ? " · updated " + new Date(DATA.updated_at).toLocaleString() : ""}</p>`;
  }

  // ---------- share ----------
  $("#shareBtn").onclick = async () => {
    const url = location.href;
    if (navigator.share) { try { await navigator.share({ title: document.title, url }); } catch (_) {} return; }
    try { await navigator.clipboard.writeText(url); toast("Link copied"); } catch (_) { prompt("Copy this link:", url); }
  };

  // ---------- boot ----------

  Promise.all([
    fetch("data/banks.json", { cache: "no-cache" }).then((r) => r.json()),
    fetch("data/news.json", { cache: "no-cache" }).then((r) => r.ok ? r.json() : { items: [] }).catch(() => ({ items: [] })),
    fetch("data/logos.json", { cache: "no-cache" }).then((r) => r.ok ? r.json() : {}).catch(() => ({})),
  ]).then(([cfg, news, logos]) => {
    CFG = cfg; DATA = news; LOGOS = logos || {};
    BANKS = Object.fromEntries(cfg.banks.map((b) => [b.id, b]));
    COUNTRIES = Object.fromEntries(cfg.countries.map((c) => [c.code, c]));
    DATA.items = (DATA.items || []).filter((i) => BANKS[i.bank_id]).sort((a, b) => b.date.localeCompare(a.date));
    const present = new Set(DATA.items.flatMap(topicsOf));
    CATEGORIES = [...THEMES.filter((t) => present.has(t)), ...[...present].filter((t) => !THEMES.includes(t)).sort()];
    window.addEventListener("hashchange", route);
    let rt; window.addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(() => {
      // Re-draw charts at the new width without resetting the view.
      document.querySelectorAll(".chart").forEach((el) => el._redraw && el._redraw());
    }, 200); });
    route();
  }).catch((err) => {
    app.innerHTML = `<div class="empty">Could not load data. ${esc(err.message)}</div>`;
  });
})();
