// "In their words": a member's statements in the Congressional Record, and news
// coverage that names them. Uses esc, fmtDate and plural from member.html.

// Labels follow the Record's own sections and markings. In the House section, words
// inserted rather than spoken are set in a different typeface, which the text
// version doesn't carry, so House proceedings can't be split the same way.
const KINDS = {
  senate: ["Senate proceedings", "Senate floor"],
  house: ["House proceedings", "House floor"],
  introduced: ["Statement on an introduced bill", "Statements on introduced bills"],
  submitted: ["Submitted for the Record, not spoken on the floor", "Submitted, not spoken"],
  extension: ["Extension of Remarks: submitted, not spoken on the floor", "Extensions of Remarks"],
};// Same words as speeches.py: letters/digits, apostrophes dropped, case ignored.
const INDEXES = { gdelt: "GDELT", mediacloud: "Media Cloud" };
const WORD = /[\p{L}\p{N}][\p{L}\p{N}\p{M}_'’]*/gu;
const crecUrl = (g) => `https://www.govinfo.gov/app/details/${g.split("-").slice(0, 4).join("-")}/${g}`;

let wordsLede = "", words, outlets, wordsTab = "floor", kind = "", wq = "", quotingOnly = false;

// Audience marker: a dot on a line from "shared only by Democrats" (-1) to
// "shared only by Republicans" (+1). Ink only.
function audience(domain) {
  if (!outlets.raters.robertson) return "";  // no ratings published yet
  const s = outlets.domains[domain]?.robertson;
  if (s === undefined) return `<span class="aud none">No audience score</span>`;
  const side = Math.abs(s) < 0.005 ? "shared equally by Democrats and Republicans"
    : `shared more by ${s < 0 ? "Democrats" : "Republicans"}`;
  const label = `Audience score ${s > 0 ? "+" : ""}${s.toFixed(2)}: ${side} (Robertson et al. 2018)`;
  return `<span class="aud" title="${label}" role="img" aria-label="${label}"><i style="left:${((s + 1) / 2) * 100}%"></i></span>`;
}

// The member's words in one section, with the spans articles quoted marked.
function markedText(turns, spans) {
  let n = 0;
  const inSpan = (i) => spans.some(([a, b]) => i >= a && i < b);
  return turns.map((paras) => paras.map((p) => {
    let html = "", last = 0, open = false;
    for (const m of p.matchAll(WORD)) {
      const on = inSpan(n++);
      if (!on && open) { html += "</mark>"; open = false; }
      html += esc(p.slice(last, m.index));
      if (on && !open) { html += "<mark>"; open = true; }
      html += esc(m[0]);
      last = m.index + m[0].length;
    }
    html += (open ? "</mark>" : "") + esc(p.slice(last));
    return `<p>${html}</p>`;
  }).join("")).join(`<p class="gap">…</p>`);
}

// Wire stories run on many sites under one headline; show each headline once, with
// the other sites that published it listed under it.
function byHeadline(arts) {
  const groups = new Map();
  for (const a of arts) {
    // Sites often append their own name ("… | WBAL Baltimore News"); ignore a short
    // trailing segment like that when comparing. The headline shown is unchanged.
    const k = a.title.replace(/\s+[|–—-]\s+[^|–—-]{1,40}$/u, "")
      .toLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim() || a.url;
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(a);
  }
  return [...groups.values()].map((g) => g.sort((x, y) => x.date.localeCompare(y.date) || x.domain.localeCompare(y.domain)));
}

function alsoOn(rest) {
  if (!rest.length) return "";
  return `<details class="also"><summary>Same headline on ${rest.length} more ${plural(rest.length, "site")}</summary>
    <ul>${rest.map((a) => `<li><a href="${esc(a.url)}">${esc(a.domain)}</a> ${audience(a.domain)} <time datetime="${a.date}">${fmtDate(a.date + "T12:00")}</time></li>`).join("")}</ul></details>`;
}

function quotedWords(turns, [a, b]) {
  const all = turns.flat().join(" ");
  const ms = [...all.matchAll(WORD)];
  return all.slice(ms[a].index, ms[b - 1].index + ms[b - 1][0].length);
}

async function openComparison(d) {
  const box = d.querySelector(".compare");
  if (box.dataset.done) return;
  box.dataset.done = 1;
  const s = words.floor.find((x) => x.g === d.dataset.g);
  let text;
  try { text = await load(`data/words/text/${encodeURIComponent(id)}/${s.g}.json`); }
  catch { box.innerHTML = `<p class="error">Couldn't load the text. <a href="${crecUrl(s.g)}">Read it on GovInfo</a>.</p>`; return; }
  const arts = s.quoted_in.map((i) => words.media[i]);
  const spans = arts.flatMap((a) => a.quotes.find((q) => q.g === s.g).spans);
  box.innerHTML = `
    <div class="official">
      <h3>Congressional Record <span class="muted">official text</span></h3>
      ${markedText(text.turns, spans)}
      <p class="muted"><a href="${crecUrl(s.g)}">${esc(s.title)}, ${fmtDate(s.date + "T12:00")}, on GovInfo</a></p>
    </div>
    <div class="outlets">
      <h3>Same wording in ${arts.length} ${plural(arts.length, "article")}</h3>
      <ol>${byHeadline(arts).map(([a, ...rest]) => `<li>
        <span class="outlet">${esc(a.domain)} ${audience(a.domain)}</span>
        <a href="${esc(a.url)}">${esc(a.title)}</a>
        <time datetime="${a.date}">${fmtDate(a.date + "T12:00")}</time>
        ${a.quotes.find((q) => q.g === s.g).spans.map((sp) => `<blockquote>${esc(quotedWords(text.turns, sp))}</blockquote>`).join("")}
        ${alsoOn(rest)}
      </li>`).join("")}</ol>
    </div>`;
}

function floorList() {
  const items = words.floor.filter((s) => (!kind || s.kind === kind) && (!wq || s.title.toLowerCase().includes(wq)));
  if (!items.length) return `<p class="empty">Nothing matches. Clear the search or pick All statements.</p>`;
  let year, html = "";
  for (const s of items) {
    const y = s.date.slice(0, 4);
    if (y !== year) { html += `<h2 class="year">${y}</h2>`; year = y; }
    const q = s.quoted_in?.length;
    const meta = `${KINDS[s.kind][0]}. ${s.words.toLocaleString()} words. <a href="${crecUrl(s.g)}">Read on GovInfo</a>`;
    const day = new Date(s.date + "T12:00").toLocaleDateString("en-US", { month: "short", day: "numeric" });
    html += q
      ? `<details class="item speech" data-g="${s.g}">
          <summary><span class="label">${day}</span><span class="doc">${esc(s.title)}</span>
          <span class="meta">${meta}. <strong>Same wording in ${q} ${plural(q, "article")}.</strong> <span class="toggle"></span></span></summary>
          <div class="compare"></div></details>`
      : `<div class="item speech"><span class="label">${day}</span><span class="doc">${esc(s.title)}</span><span class="meta">${meta}.</span></div>`;
  }
  return html;
}

// How many articles came from outlets at each audience score: one column per
// 0.05 of the scale, height relative to the tallest. Ink only.
function strip(arts) {
  if (!outlets.raters.robertson) return "";
  const scored = arts.filter((a) => outlets.domains[a.domain]?.robertson !== undefined);
  const cols = 41, counts = Array(cols).fill(0), names = Array.from({ length: cols }, () => new Set());
  for (const a of scored) {
    const c = Math.round(((outlets.domains[a.domain].robertson + 1) / 2) * (cols - 1));
    counts[c]++; names[c].add(a.domain);
  }
  const top = Math.max(1, ...counts);
  const lo = (c) => ((c / (cols - 1)) * 2 - 1).toFixed(2);
  return `<figure class="strip">
    <figcaption>Articles below by their outlet's audience score${scored.length < arts.length ? `; ${arts.length - scored.length} of ${arts.length} are from outlets with no score and aren't shown` : ""}.</figcaption>
    <div class="plot">
      <i class="mid"></i>
      ${counts.map((n, c) => n ? `<i class="col" style="left:${(c / cols) * 100}%;height:${Math.max(3, (n / top) * 100)}%"
        title="${n} ${plural(n, "article")} at about ${lo(c) > 0 ? "+" : ""}${lo(c)}: ${esc([...names[c]].slice(0, 6).join(", "))}${names[c].size > 6 ? "…" : ""}"></i>` : "").join("")}
    </div>
    <div class="ends"><span>Shared only by Democrats</span><span>Equally</span><span>Shared only by Republicans</span></div>
  </figure>`;
}

function mediaList() {
  const arts = words.media.filter((a) => (!quotingOnly || a.quotes) &&
    (!wq || `${a.title} ${a.domain}`.toLowerCase().includes(wq)));
  const all = words.media;
  const outletsN = new Set(all.map((a) => a.domain)).size;
  const quoting = all.filter((a) => a.quotes).length;
  if (!all.length) return `<p class="empty">No articles yet. Coverage is collected from the GDELT and Media Cloud news indexes and builds up from ${fmtDate(words.generated + "T12:00")}.</p>`;
  const headlines = byHeadline(all).length;
  let html = `<p class="summary">${all.length.toLocaleString()} ${plural(all.length, "article")} from ${outletsN} ${plural(outletsN, "outlet")}
    since ${fmtDate(words.media_from + "T12:00")}, under ${headlines.toLocaleString()} different ${plural(headlines, "headline")}; ${quoting} use this member's exact wording from the Congressional Record.</p>`;
  if (all.length) html += strip(all);
  if (words.capped.length) html += `<p class="muted">GDELT returned its limit of 250 articles for some days, so its coverage of those days is incomplete.</p>`;
  if (!arts.length) return html + `<p class="empty">Nothing matches.</p>`;
  html += `<ol class="articles">${byHeadline(arts).map(([a, ...rest]) => {
    const quoted = (a.quotes || []).map((q) => words.floor.find((s) => s.g === q.g)).filter(Boolean);
    return `<li>
      <time datetime="${a.date}">${fmtDate(a.date + "T12:00")}</time>
      <span class="outlet">${esc(a.domain)} ${audience(a.domain)}</span>
      <a class="headline" href="${esc(a.url)}">${esc(a.title)}</a>
      ${quoted.map((s) => `<span class="note">Same wording as the Record: <a href="#" data-open="${s.g}">${esc(s.title)}, ${fmtDate(s.date + "T12:00")}</a></span>`).join("")}
      ${a.checked ? "" : `<span class="note">We couldn't open this article, so its text wasn't checked.</span>`}
      ${a.partial ? `<span class="note">Part of this article is hidden from readers (often for subscribers); we checked only the part anyone can see.</span>` : ""}
      <span class="note">Found by ${(a.via || []).map((v) => INDEXES[v] ?? v).join(" and ")}.</span>
      ${alsoOn(rest)}
    </li>`;
  }).join("")}</ol>`;
  return html;
}

function renderWords() {
  document.querySelectorAll("#words-view .tabs button").forEach((b) => b.setAttribute("aria-pressed", b.dataset.wtab === wordsTab));
  document.getElementById("kind").hidden = wordsTab !== "floor";
  document.getElementById("quoting").parentElement.hidden = wordsTab !== "media";
  document.getElementById("words-list").innerHTML = wordsTab === "floor" ? floorList() : mediaList();
}

function setupWords(data, ratings) {
  words = data;
  outlets = ratings;
  const counts = {};
  words.floor.forEach((s) => (counts[s.kind] = (counts[s.kind] || 0) + 1));
  document.querySelector('[data-wtab="floor"]').textContent = `On the floor (${words.floor.length})`;
  document.querySelector('[data-wtab="media"]').textContent = `In the media (${words.media.length})`;
  const sel = document.getElementById("kind");
  sel.innerHTML = `<option value="">All statements (${words.floor.length})</option>` +
    Object.entries(KINDS).filter(([k]) => counts[k])
      .map(([k, [, l]]) => `<option value="${k}">${l} (${counts[k]})</option>`).join("");
  sel.addEventListener("change", () => { kind = sel.value; renderWords(); });
  document.getElementById("wq").addEventListener("input", (e) => { wq = e.target.value.trim().toLowerCase(); renderWords(); });
  document.getElementById("quoting").addEventListener("change", (e) => { quotingOnly = e.target.checked; renderWords(); });
  document.querySelectorAll("#words-view .tabs button").forEach((b) => b.addEventListener("click", () => {
    wordsTab = b.dataset.wtab; syncUrl(); renderWords();
  }));
  const list = document.getElementById("words-list");
  list.addEventListener("toggle", (e) => { if (e.target.open && e.target.dataset.g) openComparison(e.target); }, true);
  list.addEventListener("click", (e) => {
    const g = e.target.closest("[data-open]")?.dataset.open;
    if (!g) return;
    e.preventDefault();
    wordsTab = "floor"; kind = ""; wq = "";
    document.getElementById("kind").value = ""; document.getElementById("wq").value = "";
    syncUrl(); renderWords();
    const d = list.querySelector(`details[data-g="${g}"]`);
    d.open = true;
    d.scrollIntoView({ block: "start" });
  });
  const n = words.floor.length;
  wordsLede = `${n.toLocaleString()} ${plural(n, "statement")} in the Congressional Record since ` +
    `${fmtDate(words.record_from + "T12:00")}, and news coverage that names them. Each one links to its source.`;
  document.getElementById("words-stamp").textContent =
    `Record from ${fmtDate(words.record_from + "T12:00")}; built ${fmtDate(words.generated + "T12:00")}.`;
  renderWords();
}
