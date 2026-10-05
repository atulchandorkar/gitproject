/* GCC Bank AI Tracker – static dashboard (no build step). */
(() => {
  "use strict";

  const app = document.getElementById("app");

  // Brand
  const BRAND = { name: "GCC Banking", word: "AI Pulse Monitor", tagline: "Tracking AI in Gulf banks, daily" };
  document.title = `${BRAND.name} – ${BRAND.word}`;

  const SHORT_NAME = { SA: "KSA" };  // keeps the flag strip readable on small phones
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
  let REPORTS = {};
  let SECTOR = { items: [], updated_at: null }, SECTOR_CFG = { categories: [], publishers: [] };

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
    if (view === "sector") return setActiveTab("sector"), renderSector(q);
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
    return `<article class="card news-card">
      <div class="meta">
        ${showBank ? `<a class="bank-chip" href="#/bank/${b.id}">${avatar(b, "avatar sm")}<span class="bank">${esc(b.short)}</span></a>${flag(c)}<span class="dot"></span>` : ""}
        <time datetime="${i.date}">${fmtDate(i.date)}</time>
        <span class="dot"></span><span class="cat">${esc(i.category)}</span>${extraTopics}
      </div>
      ${isAR(i) ? `<div class="ar-chip">📘 From ${esc(b.short)}'s Annual Report ${i.report.year} · p. ${i.report.page}</div>` : ""}
      <h3><a href="${esc(i.source_url)}" target="_blank" rel="noopener">${esc(i.title)}</a></h3>
      <p>${esc(i.summary && i.summary.trim().length >= 40 ? i.summary
        : `${b.name}: ${(i.source_title || i.title).replace(/\.$/, "")}. Reported by ${srcs[0].name || "the source"} on ${fmtDate(i.date)}; open the source for full details.`)}</p>
      ${i.impact ? `<div class="impact">📈 ${esc(i.impact)}</div>` : ""}
      ${tags || (i.partners && i.partners.length) ? `<div class="tags">${tags}${i.partners && i.partners.length ? `<span class="tag">🤝 ${esc(i.partners.join(", "))}</span>` : ""}</div>` : ""}
      ${isAR(i) ? `<blockquote class="ar-quote" dir="auto">“${esc(i.report.quote)}”</blockquote>
        <a class="pdf-btn" href="${esc(i.source_url)}" target="_blank" rel="noopener"><span>📘</span><b>Annual Report ${i.report.year} (PDF)</b><small>opens p. ${i.report.page} · ${esc(b.domain)}</small><span class="pdf-go">↗</span></a>` : ""}
      ${sourceBlock(i, VERIFY_LABEL)}
    </article>`;
  }

  const AR_FILTER = "annual-reports";
  const isAR = (i) => i.source_type === "annual_report" && i.report;
  // Verification badge, original headline and every source link (shared by bank news and sector cards).
  function sourceBlock(i, labels) {
    const srcs = sourcesOf(i);
    if (isAR(i)) return `<div class="sources"><div class="verified" title="The quote was found word for word in the report and every claim was fact-checked against that page">✓ Bank's own annual report · quote checked on p. ${i.report.page}</div></div>`;
    const v = i.verification && labels[i.verification.level];
    const outlets = new Set(srcs.map((s) => (s.name || "").toLowerCase()).filter(Boolean)).size;
    const badge = v ? `<div class="verified" title="Passed the source, numbers and AI fact-checks">✓ ${esc(i.verification.level === "corroborated" ? `Confirmed by ${outlets} outlets` : v)}${i.verification.evidence === "article" ? " · checked against full article" : " · checked against headline"}</div>` : "";
    const orig = i.source_title && i.source_title.trim() !== i.title.trim()
      ? `<div class="orig"><span>Original headline:</span> <span dir="auto">${esc(i.source_title)}</span></div>` : "";
    const label = (s, n) => {
      if (s.name) return s.name;
      try { const h = new URL(s.url).hostname.replace(/^www\./, ""); return h === "news.google.com" ? `News source ${n + 1}` : h; }
      catch (_) { return `Source ${n + 1}`; }
    };
    const srcLinks = srcs.map((s, n) => `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(label(s, n))} ↗</a>`).join("");
    return `<div class="sources">${badge}${orig}<div class="src-list"><span>${srcs.length > 1 ? "Sources" : "Source"}:</span>${srcLinks}</div></div>`;
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
  function chartBlock(id, title, sub = "Announcements per month") {
    return `<div class="card chart-card"><div class="chart-head"><b>${esc(title)}</b><span>${esc(sub)}</span></div>
      <div class="chart" id="${id}"></div></div>`;
  }
  function mountChart(id, items, months = 24, unit = "announcement") {
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
    el._redraw = () => mountChart(id, items, months, unit);
    const wrap = document.createElement("div");
    wrap.style.position = "relative";
    wrap.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Monthly announcements, last ${months} months">${svg}</svg><div class="tooltip"></div>`;
    el.replaceChildren(wrap);
    const tip = wrap.querySelector(".tooltip");
    const show = (e) => {
      const t = e.target.closest("[data-i]"); if (!t) return;
      const b = buckets[+t.dataset.i];
      el.querySelectorAll(".bar").forEach((p) => p.classList.toggle("hl", p.dataset.i === t.dataset.i));
      tip.textContent = `${b.label.replace(" ", " 20")} · ${plural(b.n, unit)}`;
      const rect = wrap.getBoundingClientRect();
      tip.style.left = `${Math.min(Math.max(padL + (+t.dataset.i + 0.5) * band, 70), rect.width - 70)}px`;
      tip.style.top = `${Math.max(y(b.n), 30)}px`;
      tip.classList.add("show");
    };
    wrap.addEventListener("pointermove", show);
    wrap.addEventListener("pointerdown", show);
    wrap.addEventListener("pointerleave", () => { tip.classList.remove("show"); el.querySelectorAll(".bar").forEach((p) => p.classList.remove("hl")); });
  }

  // Bank picker: a bottom sheet with logos, flags and counts (native dropdowns can't show images on phones).
  function bankPicker({ country, selected, onPick }) {
    const counts = countsByBank(DATA.items);
    const sheet = document.createElement("div");
    sheet.className = "sheet-wrap";
    sheet.innerHTML = `<div class="sheet" role="dialog" aria-modal="true" aria-label="Choose a bank">
      <div class="sheet-head"><b>Choose a bank</b><button class="sheet-x" aria-label="Close">✕</button></div>
      <label class="search sheet-search"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/></svg>
        <input type="search" placeholder="Find a bank…" aria-label="Find a bank"></label>
      <div class="sheet-list"></div></div>`;
    document.body.appendChild(sheet);
    document.body.classList.add("no-scroll");
    const close = () => { sheet.remove(); document.body.classList.remove("no-scroll"); };
    const draw = (needle) => {
      const row = (b) => `<button class="pk-row" data-id="${b.id}" aria-pressed="${b.id === selected}">${avatar(b)}
        <span class="pk-info"><span class="pk-name">${esc(b.name)}</span><span class="pk-sub">${flag(COUNTRIES[b.country])} ${esc(b.short)} · ${esc(CFG.types[b.type] || b.type)}</span></span>
        <span class="pk-n">${counts[b.id] || 0}</span></button>`;
      const groups = CFG.countries.filter((c) => !country || c.code === country).map((c) => {
        const banks = CFG.banks.filter((b) => b.country === c.code && (!needle ||
          `${b.name} ${b.short} ${b.name_ar || ""} ${(b.aliases || []).join(" ")}`.toLowerCase().includes(needle)))
          .sort((a, b) => featuredFirst(a, b) || (counts[b.id] || 0) - (counts[a.id] || 0) || a.short.localeCompare(b.short));
        return banks.length ? `<div class="pk-group">${flag(c)} ${esc(c.name)}</div>${banks.map(row).join("")}` : "";
      }).join("");
      sheet.querySelector(".sheet-list").innerHTML = (needle ? "" : `<button class="pk-row pk-all" data-id="" aria-pressed="${!selected}">
        <span class="avatar"><span class="ini">ALL</span></span><span class="pk-info"><span class="pk-name">All banks</span>
        <span class="pk-sub">${country ? esc(COUNTRIES[country].name) : "All GCC"}</span></span></button>`) + (groups || `<div class="empty">No bank matches “${esc(needle)}”.</div>`);
    };
    draw("");
    sheet.addEventListener("click", (e) => {
      if (e.target === sheet || e.target.closest(".sheet-x")) return close();
      const r = e.target.closest(".pk-row"); if (!r) return;
      close(); onPick(r.dataset.id);
    });
    const input = sheet.querySelector("input");
    input.oninput = () => draw(input.value.trim().toLowerCase());
    document.addEventListener("keydown", function esc_(e) { if (e.key === "Escape") { close(); document.removeEventListener("keydown", esc_); } });
  }

  // ---------- Feed ----------
  function renderFeed(q) {
    const st = {
      country: q.get("country") || "", bank: q.get("bank") || "", cat: q.get("cat") || "",
      period: q.get("period") || "all", q: q.get("q") || "", view: q.get("view") === "insights" ? "insights" : "",
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
            return `<button class="hc" data-country="${c.code}" aria-pressed="${st.country === c.code}">${flag(c, "hc-flag")}<span class="hc-n">${n}</span><span class="hc-l">${esc(SHORT_NAME[c.code] || c.name)}</span><span class="hc-bar"><span style="width:${(n / maxC) * 100}%"></span></span></button>`;
          }).join("")}
        </div>
      </section>
      <div class="filters">
        <label class="search"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/></svg>
          <input id="fq" type="search" placeholder="Search news, partners, tags…" value="${esc(st.q)}" aria-label="Search"></label>
        <button type="button" class="picker-btn" id="fbank" aria-haspopup="dialog" aria-label="Bank"></button>
        <select id="fcat" aria-label="Theme"><option value="">All themes</option><option value="${AR_FILTER}" ${st.cat === AR_FILTER ? "selected" : ""}>📘 Annual reports</option>${CATEGORIES.map((c) => `<option ${c === st.cat ? "selected" : ""}>${esc(c)}</option>`).join("")}</select>
        <select id="fperiod" aria-label="Period">${PERIODS.map(([v, l]) => `<option value="${v}" ${v === st.period ? "selected" : ""}>${l}</option>`).join("")}</select>
      </div>
      <div class="seg" role="tablist" id="viewSwitch">
        <button role="tab" data-view="" aria-selected="${st.view !== "insights"}">📰 News</button>
        <button role="tab" data-view="insights" aria-selected="${st.view === "insights"}">📊 Insights</button>
      </div>
      <div id="insightsPane" ${st.view === "insights" ? "" : "hidden"}>
        <div style="margin-top:10px">${chartBlock("feedChart", "Activity")}</div>
        <div class="insights">
          <div class="card"><div class="chart-head"><b>AI themes</b><span>tap to filter</span></div><div id="themeBars"></div></div>
          <div class="card"><div class="chart-head"><b>Top tech partners</b><span>tap to filter</span></div><div id="partnerBars"></div></div>
          <div class="card"><div class="chart-head"><b>Where it comes from</b><span>tap to filter</span></div><div id="srcBars"></div></div>
        </div>
      </div>
      <div id="newsPane" ${st.view === "insights" ? "hidden" : ""}>
      <div class="result-line"><span id="resCount"></span><button class="linklike" id="clearBtn" hidden>Clear filters</button></div>
      <div class="news-list" id="feedList"></div>
      <button class="more-btn" id="moreBtn" hidden>Show more</button>
      </div>
      <p class="updated">${DATA.updated_at ? "Updated " + new Date(DATA.updated_at).toLocaleString() : ""} · checked daily</p>`;

    const fillBanks = () => {
      if (st.bank && st.country && BANKS[st.bank] && BANKS[st.bank].country !== st.country) st.bank = "";
      const b = BANKS[st.bank];
      $("#fbank").innerHTML = b ? `${avatar(b, "avatar xs")}<span class="pk-l">${esc(b.short)}</span><span class="pk-c">▾</span>`
        : `<span class="pk-l">All banks</span><span class="pk-c">▾</span>`;
    };
    const pickBank = () => bankPicker({ country: st.country, selected: st.bank, onPick: (id) => { st.bank = id; fillBanks(); update(); } });

    let shown = PAGE, lastList = [];
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
      const list = base.filter((i) => !st.cat || (st.cat === AR_FILTER ? isAR(i) : hasTopic(i, st.cat)));
      const banksActive = new Set(list.map((i) => i.bank_id)).size;
      const last30 = list.filter((i) => i.date >= isoDaysAgo(30)).length;
      const countries = new Set(list.map((i) => i.country)).size;
      $("#heroKpis").innerHTML = [["news", list.length.toLocaleString(), "AI news"], ["bank", banksActive, "banks"],
        ["globe", countries, "countries"], ["bolt", last30, "last 30 days"]]
        .map(([k, v, l]) => `<div class="kpi">${icon(k)}<b>${v}</b><span>${l}</span></div>`).join("");
      $("#resCount").textContent = list.length ? `Showing ${Math.min(shown, list.length)} of ${plural(list.length, "item")}` : "";
      $("#clearBtn").hidden = !(st.country || st.bank || st.cat || st.q || st.period !== "all");
      $("#feedList").innerHTML = list.length ? list.slice(0, shown).map((i) => newsCard(i)).join("") : emptyState();
      $("#moreBtn").hidden = list.length <= shown;
      $("#moreBtn").onclick = () => { shown += PAGE; update(false); };
      lastList = list;
      if (st.view === "insights") mountChart("feedChart", list);
      // Themes are counted on everything except the theme filter itself, so all bars stay comparable.
      $("#themeBars").innerHTML = barList("themeList", countBy(base, topicsOf), st.cat, "theme", base.length);
      $("#partnerBars").innerHTML = barList("partnerList", countBy(list, partnersOf).slice(0, 8), st.q, "partner");
      $("#srcBars").innerHTML = barList("srcList", countBy(base, (i) => [isAR(i) ? "📘 Bank annual reports" : "📰 News & bank releases"]),
        st.cat === AR_FILTER ? "📘 Bank annual reports" : "", "src", base.length);
    };
    const showView = (v) => {
      st.view = v;
      $("#insightsPane").hidden = v !== "insights"; $("#newsPane").hidden = v === "insights";
      document.querySelectorAll("#viewSwitch [data-view]").forEach((x) => x.setAttribute("aria-selected", x.dataset.view === v));
      update(false);
      if (v === "insights") mountChart("feedChart", lastList);  // chart needs a visible container to size itself
    };
    $("#viewSwitch").addEventListener("click", (e) => { const x = e.target.closest("[data-view]"); if (x) showView(x.dataset.view); });
    $("#themeBars").addEventListener("click", (e) => {
      const x = e.target.closest("[data-theme]"); if (!x) return;
      st.cat = x.dataset.theme === st.cat ? "" : x.dataset.theme; $("#fcat").value = st.cat; showView("");
    });
    $("#srcBars").addEventListener("click", (e) => {
      const x = e.target.closest("[data-src]"); if (!x) return;
      st.cat = x.dataset.src.includes("annual") && st.cat !== AR_FILTER ? AR_FILTER : ""; $("#fcat").value = st.cat; showView("");
    });
    $("#partnerBars").addEventListener("click", (e) => {
      const x = e.target.closest("[data-partner]"); if (!x) return;
      st.q = x.dataset.partner === st.q ? "" : x.dataset.partner; $("#fq").value = st.q; showView("");
    });

    $("#countryChips").addEventListener("click", (e) => {
      const btn = e.target.closest("[data-country]"); if (!btn) return;
      st.country = btn.dataset.country;
      document.querySelectorAll("#countryChips [data-country]").forEach((c) => c.setAttribute("aria-pressed", c === btn));
      fillBanks(); update();
    });
    $("#fbank").onclick = pickBank;
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
  // Featured banks (config "featured": true, e.g. QIB) are listed first everywhere.
  const featuredFirst = (a, b) => (b.featured ? 1 : 0) - (a.featured ? 1 : 0);
  const countsByBank = (items) => items.reduce((m, i) => ((m[i.bank_id] = (m[i.bank_id] || 0) + 1), m), {});

  function renderCountry(code) {
    const c = COUNTRIES[code];
    const items = DATA.items.filter((i) => i.country === code);
    const counts = countsByBank(items);
    const banks = CFG.banks.filter((b) => b.country === code)
      .sort((a, b) => featuredFirst(a, b) || (counts[b.id] || 0) - (counts[a.id] || 0) || TYPE_ORDER[a.type] - TYPE_ORDER[b.type]);
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
          .sort((a, b) => featuredFirst(a, b) || (counts[b.id] || 0) - (counts[a.id] || 0) || a.short.localeCompare(b.short));
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
      ${(REPORTS[id] || []).length ? `<div class="section-title">📘 Annual reports</div><div class="ar-list">${REPORTS[id].map((r) =>
        `<a class="pdf-btn" href="${esc(r.url)}" target="_blank" rel="noopener"><span>📘</span><b>Annual Report ${r.year}</b><small>${r.items} AI disclosure${r.items === 1 ? "" : "s"} · ${r.pages} pages · PDF</small><span class="pdf-go">↗</span></a>`).join("")}</div>` : ""}
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

  // ---------- GCC Banking Sector Insights ----------
  const SECTOR_LABEL = { official: "Publisher's own release", trusted: "Trusted outlet", corroborated: "Confirmed by several outlets" };
  const SECTOR_ICON = (c) => (SECTOR_CFG.categories.find((x) => x.id === c) || {}).icon || "•";
  const SECTOR_SHORT = { "Studies & Surveys": "Studies", "Maturity & Rankings": "Rankings", "Regulation & Guidance": "Regulation",
    "Market Data": "Market data", "Expert Views": "Expert views", "Events & Initiatives": "Initiatives" };
  function publisherOf(name) {
    const n = (name || "").toLowerCase();
    if (!n) return null;
    const p = SECTOR_CFG.publishers.find((x) => [x.name, x.short, ...(x.aliases || [])].some((a) => {
      const l = a.toLowerCase(); return n === l || n.startsWith(l + " ") || l.startsWith(n + " ") || n.startsWith(l + ":"); }));
    if (p) return p;
    const b = CFG.banks.find((x) => x.type === "central" && [x.name, x.short, ...(x.aliases || [])].some((a) => n === a.toLowerCase() || n.includes(a.toLowerCase())));
    return b ? { name: b.name, short: b.short, bank: b } : null;
  }
  const pubShort = (i) => { const p = publisherOf(i.publisher); return p ? p.short : (i.publisher || ""); };
  function pubAvatar(i) {
    const p = publisherOf(i.publisher);
    if (p && p.bank) return avatar(p.bank, "avatar sm");
    const ini = esc((pubShort(i) || i.source_name || "?").replace(/[^A-Za-z0-9&]/g, "").slice(0, 3).toUpperCase() || "📊");
    const img = p && p.domain ? `<img src="https://www.google.com/s2/favicons?domain=${esc(p.domain)}&sz=64" alt="" loading="lazy" onerror="this.parentNode.classList.remove('has-logo');this.remove()">` : "";
    return `<span class="avatar sm${img ? " has-logo" : ""}" aria-hidden="true">${img}<span class="ini">${ini}</span></span>`;
  }
  const geoOf = (i) => (i.countries && i.countries.length ? i.countries : ["GCC"]);
  function geoChips(i) {
    const cs = geoOf(i).filter((c) => COUNTRIES[c]);
    return cs.length && !geoOf(i).includes("GCC") ? cs.map((c) => flag(COUNTRIES[c])).join("") : `<span class="geo">GCC-wide</span>`;
  }

  function sectorCard(i) {
    const pub = pubShort(i);
    const stats = (i.key_stats || []).slice(0, 3);
    return `<article class="card news-card sector-card">
      <div class="meta">
        ${pub ? `<span class="bank-chip">${pubAvatar(i)}<span class="bank">${esc(pub)}</span></span><span class="dot"></span>` : ""}
        <time datetime="${i.date}">${fmtDate(i.date)}</time><span class="dot"></span>${geoChips(i)}
      </div>
      <div class="sec-cat">${SECTOR_ICON(i.category)} ${esc(i.category)}</div>
      <h3><a href="${esc(i.source_url)}" target="_blank" rel="noopener" dir="auto">${esc(i.title)}</a></h3>
      <p>${esc(i.summary)}</p>
      ${stats.length ? `<div class="kstats kstats-${stats.length}">${stats.map((st) =>
        `<div class="kstat" title="${esc(st.quote)}"><b>${esc(st.value)}</b><span>${esc(st.label)}</span></div>`).join("")}</div>` : ""}
      ${i.pdf ? `<a class="pdf-btn" href="${esc(i.pdf.url)}" target="_blank" rel="noopener"><span>📄</span><b>Report PDF</b><small>official file · ${esc(i.pdf.host)}</small><span class="pdf-go">↗</span></a>` : ""}
      ${sourceBlock(i, SECTOR_LABEL)}
    </article>`;
  }

  function renderSector(q) {
    const st = { cat: q.get("cat") || "", country: q.get("country") || "", pub: q.get("pub") || "", q: q.get("q") || "",
      view: q.get("view") === "charts" ? "charts" : "" };
    const all = SECTOR.items;
    const cats = SECTOR_CFG.categories.map((c) => c.id);
    const updated = SECTOR.updated_at ? new Date(SECTOR.updated_at).toLocaleString([], { timeZone: "Asia/Qatar", day: "numeric", month: "short", hour: "numeric", minute: "2-digit" }) : "";
    const pubs = countBy(all, (i) => [pubShort(i)]).map(([k]) => k);
    app.innerHTML = `
      <section class="hero sector-hero">
        ${NETWORK}
        <div class="hero-eyebrow"><span class="live-dot"></span>Last 12 months${updated ? ` · updated ${esc(updated)}` : ""}</div>
        <h1 class="hero-title"><span class="hero-kicker">GCC Banking</span><b>Sector Insights</b></h1>
        <p class="hero-tag">AI across GCC banking: studies, rankings, regulation</p>
        <div class="hero-kpis" id="secKpis"></div>
        <div class="cat-grid" id="secCats">
          ${cats.map((c) => `<button class="cat-tile" data-cat="${esc(c)}" aria-pressed="${st.cat === c}">
            <span class="ct-ic">${SECTOR_ICON(c)}</span><b>${all.filter((i) => i.category === c).length}</b><span class="ct-l">${esc(SECTOR_SHORT[c] || c)}</span></button>`).join("")}
        </div>
      </section>
      <div id="statRailWrap"></div>
      <div class="filters">
        <label class="search"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/></svg>
          <input id="sq" type="search" placeholder="Search studies, firms, topics…" value="${esc(st.q)}" aria-label="Search"></label>
        <select id="scountry" aria-label="Country"><option value="">All GCC</option><option value="GCC" ${st.country === "GCC" ? "selected" : ""}>GCC-wide studies</option>
          ${CFG.countries.map((c) => `<option value="${c.code}" ${st.country === c.code ? "selected" : ""}>${esc(c.name)}</option>`).join("")}</select>
        <select id="spub" aria-label="Publisher"><option value="">All publishers</option>${pubs.filter(Boolean).map((p) => `<option ${p === st.pub ? "selected" : ""}>${esc(p)}</option>`).join("")}</select>
        <select id="scat" aria-label="Category"><option value="">All categories</option>${cats.map((c) => `<option value="${esc(c)}" ${c === st.cat ? "selected" : ""}>${esc(SECTOR_SHORT[c] || c)}</option>`).join("")}</select>
      </div>
      <div class="seg" role="tablist" id="secSwitch">
        <button role="tab" data-view="" aria-selected="${st.view !== "charts"}">📑 Insights</button>
        <button role="tab" data-view="charts" aria-selected="${st.view === "charts"}">📊 Charts</button>
      </div>
      <div id="secCharts" ${st.view === "charts" ? "" : "hidden"}>
        <div style="margin-top:10px">${chartBlock("secChart", "Sector activity", "Insights per month")}</div>
        <div class="insights">
          <div class="card"><div class="chart-head"><b>By category</b><span>tap to filter</span></div><div id="secCatBars"></div></div>
          <div class="card"><div class="chart-head"><b>Top publishers</b><span>tap to filter</span></div><div id="secPubBars"></div></div>
          <div class="card"><div class="chart-head"><b>By country</b><span>tap to filter</span></div><div id="secGeoBars"></div></div>
        </div>
      </div>
      <div id="secList" ${st.view === "charts" ? "hidden" : ""}>
        <div class="result-line"><span id="secCount"></span><button class="linklike" id="secClear" hidden>Clear filters</button></div>
        <div class="news-list" id="secItems"></div>
        <button class="more-btn" id="secMore" hidden>Show more</button>
      </div>
      <p class="updated">Studies, rankings, regulation and market data on AI in GCC banking · checked daily</p>`;

    const geoName = (c) => (c === "GCC" ? "GCC-wide" : COUNTRIES[c] ? COUNTRIES[c].name : c);
    let shown = PAGE, last = [];
    const update = (reset = true) => {
      if (reset) shown = PAGE;
      const qp = new URLSearchParams();
      Object.entries(st).forEach(([k, v]) => { if (v) qp.set(k, v); });
      setQuery(qp);
      const needle = st.q.trim().toLowerCase();
      const base = all.filter((i) => (!st.country || geoOf(i).includes(st.country)) && (!st.pub || pubShort(i) === st.pub) &&
        (!needle || [i.title, i.summary, i.publisher, i.source_title, i.category, ...(i.key_stats || []).map((k) => `${k.value} ${k.label}`)]
          .join(" ").toLowerCase().includes(needle)));
      const list = base.filter((i) => !st.cat || i.category === st.cat);
      last = list;
      const figures = list.flatMap((i) => (i.key_stats || []).map((k) => ({ ...k, i })));
      $("#secKpis").innerHTML = [["news", list.length, "insights"], ["bank", new Set(list.map(pubShort).filter(Boolean)).size, "publishers"],
        ["bolt", figures.length, "key figures"], ["globe", list.filter((i) => i.date >= isoDaysAgo(90)).length, "last 90 days"]]
        .map(([k, v, l]) => `<div class="kpi">${icon(k)}<b>${v}</b><span>${l}</span></div>`).join("");
      document.querySelectorAll("#secCats .cat-tile").forEach((x) => {
        x.setAttribute("aria-pressed", x.dataset.cat === st.cat);
        x.querySelector("b").textContent = base.filter((i) => i.category === x.dataset.cat).length;
      });
      $("#statRailWrap").innerHTML = figures.length ? `<div class="section-title">What the studies say</div>
        <div class="stat-rail">${figures.slice(0, 12).map((f) => `<a class="stat-card" href="${esc(f.i.source_url)}" target="_blank" rel="noopener" title="${esc(f.quote)}">
          <b>${esc(f.value)}</b><span class="sl">${esc(f.label)}</span><span class="sp">${esc(pubShort(f.i) || f.i.source_name)} · ${fmtDate(f.i.date)}</span></a>`).join("")}</div>` : "";
      $("#secCount").textContent = list.length ? `Showing ${Math.min(shown, list.length)} of ${plural(list.length, "insight")}` : "";
      $("#secClear").hidden = !(st.cat || st.country || st.pub || st.q);
      $("#secItems").innerHTML = list.length ? list.slice(0, shown).map(sectorCard).join("") : `<div class="empty card"><div class="big">${all.length ? "🔍" : "⏳"}</div>
        <p><b>${all.length ? "No matching insights" : "Sector insights are being collected"}</b></p>
        <p>${all.length ? "Try widening the filters." : "The first run loads the last 12 months of studies, rankings and regulation on AI in GCC banking. Check back after the next daily update."}</p></div>`;
      $("#secMore").hidden = list.length <= shown;
      $("#secMore").onclick = () => { shown += PAGE; update(false); };
      $("#secCatBars").innerHTML = barList("secCatList", cats.map((c) => [c, base.filter((i) => i.category === c).length]).filter(([, n]) => n), st.cat, "cat", base.length);
      $("#secPubBars").innerHTML = barList("secPubList", countBy(list, (i) => [pubShort(i)]).slice(0, 8), st.pub, "pub");
      $("#secGeoBars").innerHTML = barList("secGeoList", countBy(list, geoOf).map(([k, n]) => [geoName(k), n, k]), geoName(st.country), "geo");
      if (st.view === "charts") mountChart("secChart", list, 12, "insight");
    };
    const showView = (v) => {
      st.view = v;
      $("#secCharts").hidden = v !== "charts"; $("#secList").hidden = v === "charts";
      document.querySelectorAll("#secSwitch [data-view]").forEach((x) => x.setAttribute("aria-selected", x.dataset.view === v));
      update(false);
    };
    const setCat = (c) => { st.cat = c === st.cat ? "" : c; $("#scat").value = st.cat; };
    $("#secSwitch").addEventListener("click", (e) => { const x = e.target.closest("[data-view]"); if (x) showView(x.dataset.view); });
    $("#secCats").addEventListener("click", (e) => { const x = e.target.closest("[data-cat]"); if (!x) return; setCat(x.dataset.cat); showView(""); });
    $("#secCatBars").addEventListener("click", (e) => { const x = e.target.closest("[data-cat]"); if (!x) return; setCat(x.dataset.cat); showView(""); });
    $("#secPubBars").addEventListener("click", (e) => { const x = e.target.closest("[data-pub]"); if (!x) return;
      st.pub = x.dataset.pub === st.pub ? "" : x.dataset.pub; $("#spub").value = st.pub; showView(""); });
    $("#secGeoBars").addEventListener("click", (e) => { const x = e.target.closest("[data-geo]"); if (!x) return;
      const code = x.dataset.geo === "GCC-wide" ? "GCC" : (CFG.countries.find((c) => c.name === x.dataset.geo) || {}).code || "";
      st.country = code === st.country ? "" : code; $("#scountry").value = st.country; showView(""); });
    $("#scountry").onchange = (e) => { st.country = e.target.value; update(); };
    $("#spub").onchange = (e) => { st.pub = e.target.value; update(); };
    $("#scat").onchange = (e) => { st.cat = e.target.value; update(); };
    let t; $("#sq").oninput = (e) => { clearTimeout(t); t = setTimeout(() => { st.q = e.target.value; update(); }, 180); };
    $("#secClear").onclick = () => { location.hash = "#/sector"; };
    update();
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
        <h2>Bank annual reports</h2>
        <p>The tracker also reads each bank's own <b>annual report</b> (the last two years, PDF from the bank's website) and extracts the concrete AI
        implementations it discloses. Each one appears as a 📘 card tagged to the bank, with the exact quote and a button that opens the official PDF at
        that page. A disclosure is shown only if its quote is found word for word in the report, its numbers are on that page, and an independent
        AI fact-check confirms it. Filter with “📘 Annual reports” in the theme menu; each bank page lists its report PDFs.</p>
        <h2>GCC Banking Sector Insights</h2>
        <p>A separate tab for AI across GCC banking as a whole rather than one bank: <b>studies &amp; surveys</b> (McKinsey, BCG, PwC,
        Deloitte, Accenture, EY, KPMG, IDC, Gartner …), <b>maturity indices &amp; rankings</b>, <b>regulation &amp; guidance</b> from GCC
        central banks and regulators, <b>market data</b>, <b>expert views</b> and <b>sector events &amp; initiatives</b> from the last 12 months.
        The same checks apply: the source must be about AI <i>and</i> banking <i>and</i> the GCC, one bank's own news stays in the News tab,
        and a key figure is shown only when its exact wording appears in the source.</p>
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
    fetch("data/sector.json", { cache: "no-cache" }).then((r) => r.ok ? r.json() : { items: [] }).catch(() => ({ items: [] })),
    fetch("data/sector_sources.json", { cache: "no-cache" }).then((r) => r.ok ? r.json() : null).catch(() => null),
    fetch("data/annual_reports.json", { cache: "no-cache" }).then((r) => r.ok ? r.json() : {}).catch(() => ({})),
  ]).then(([cfg, news, logos, sector, sectorCfg, reports]) => {
    REPORTS = reports || {};
    CFG = cfg; DATA = news; LOGOS = logos || {};
    SECTOR = sector || { items: [] }; SECTOR.items = (SECTOR.items || []).sort((a, b) => b.date.localeCompare(a.date));
    if (sectorCfg) SECTOR_CFG = sectorCfg;
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
