const PodcastArtifacts = (() => {
  const data = () => JSON.parse(document.getElementById("episode-data").textContent);
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[ch]));
  const qs = (sel, root = document) => root.querySelector(sel);
  const qsa = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const copy = (text) => navigator.clipboard?.writeText(text).catch(() => {});
  const extAttrs = (url) => /^https?:/i.test(url || "") ? ' target="_blank" rel="noopener noreferrer"' : '';

  const fmtNumber = (n) => Number(n || 0).toLocaleString();
  const fmtDuration = (sec) => {
    sec = Number(sec || 0);
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    if (h) return `${h}h ${m}m`;
    return `${m}m`;
  };
  const fmtDate = (iso) => {
    if (!iso) return "";
    // Bare YYYY-MM-DD is parsed as UTC by Date(); rebuild as local to avoid timezone shift.
    const ymdMatch = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
    const d = ymdMatch
      ? new Date(Number(ymdMatch[1]), Number(ymdMatch[2]) - 1, Number(ymdMatch[3]))
      : new Date(iso);
    if (Number.isNaN(+d)) return "";
    return d.toLocaleDateString("en-US", { year: "numeric", month: "long", day: "numeric" });
  };
  const fmtShortTs = (ts) => (ts || "").replace(/^00:/, "");

  function topbar(model, mode) {
    const briefingActive = mode === "glance" ? "active" : "";
    const transcriptActive = mode === "transcript" ? "active" : "";
    const showNotes = (model.links || []).find((l) => /show notes/i.test(l.label || ""));
    const officialTranscript = (model.links || []).find((l) => /official transcript/i.test(l.label || ""));
    return `
      <header class="topbar">
        <div class="topbar-inner">
          <div class="brand">
            <strong>${esc(model.episode.short_title || model.episode.title)}</strong>
            <span>${esc(model.episode.podcast_title)}${model.episode.episode_number ? ` · #${esc(model.episode.episode_number)}` : ""}</span>
          </div>
          <nav class="folder-tabs" role="tablist" aria-label="Episode views">
            <a class="folder-tab ${briefingActive}" href="podcast-at-a-glance.html" role="tab" aria-selected="${mode === "glance"}">Briefing</a>
            <a class="folder-tab ${transcriptActive}" href="annotated-transcript.html" role="tab" aria-selected="${mode === "transcript"}">Transcript</a>
            ${model.episode.episode_url ? `<a class="folder-tab external listen" href="${esc(model.episode.episode_url)}"${extAttrs(model.episode.episode_url)}>Listen <span class="ext" aria-hidden="true">↗</span></a>` : ""}
            ${showNotes ? `<a class="folder-tab external" href="${esc(showNotes.url)}"${extAttrs(showNotes.url)}>Show notes <span class="ext" aria-hidden="true">↗</span></a>` : ""}
            ${officialTranscript ? `<a class="folder-tab external" href="${esc(officialTranscript.url)}"${extAttrs(officialTranscript.url)}>Official transcript <span class="ext" aria-hidden="true">↗</span></a>` : ""}
          </nav>
        </div>
      </header>`;
  }

  function transcriptRail(model) {
    const chapters = model.chapters || [];
    const items = chapters.map((c) => `
      <li>
        <a class="chapter-nav-item" href="#${esc(c.anchor_id)}" data-spy="${esc(c.anchor_id)}" data-jump-target="#${esc(c.anchor_id)}">
          <time>${esc(fmtShortTs(c.timestamp))}</time>
          <span>${esc(c.title)}</span>
        </a>
      </li>`).join("");
    return `
      <aside class="t-rail" aria-label="Transcript navigation">
        <div class="t-rail-search">
          <div class="search-wrap">
            <input class="search" id="global-search" type="search" placeholder="Search transcript" autocomplete="off" spellcheck="false">
            <div class="search-nav" id="search-nav" hidden>
              <button type="button" data-search-prev aria-label="Previous match (Shift+Enter or ←)">‹</button>
              <button type="button" data-search-next aria-label="Next match (Enter or →)">›</button>
            </div>
          </div>
          <div class="search-status" id="search-status" aria-live="polite"></div>
        </div>
        ${chapters.length ? `<p class="t-rail-label">Chapters</p><ol class="t-rail-list">${items}</ol>` : ""}
      </aside>`;
  }

  // ─── At-a-glance studio (investor briefing) ──────────────────────

  const CAT_LABELS = {
    person: "People",
    company: "Companies",
    organization: "Organizations",
    concept: "Concepts & tech",
    book: "Books mentioned",
    health: "Health & longevity",
  };
  const CAT_ORDER = ["person", "company", "organization", "concept", "book", "health"];

  function colorizeTitle(title) {
    if (!title) return "";
    const parts = title.split(/,\s*/);
    if (parts.length === 1) return `<span class="ink">${esc(title)}</span>`;
    const classes = ["ink", "a", "b", "c"];
    return parts.map((p, i) => {
      const cls = classes[Math.min(i, classes.length - 1)];
      const sep = i < parts.length - 1 ? ", " : "";
      return `<span class="${cls}">${esc(p.trim())}${sep}</span>`;
    }).join("");
  }

  function groupTerms(terms) {
    const grouped = new Map();
    (terms || []).forEach((t) => {
      const cat = t.category || "other";
      const list = grouped.get(cat) || [];
      list.push(t);
      grouped.set(cat, list);
    });
    return grouped;
  }

  function titleCaseCategory(cat) {
    if (!cat) return "Other";
    const cleaned = cat.replace(/[_-]+/g, " ");
    return cleaned.charAt(0).toUpperCase() + cleaned.slice(1) + (cleaned.endsWith("s") ? "" : "s");
  }

  function renderInspectorSections(model) {
    const grouped = groupTerms(model.terms);
    const sections = [];
    // Only filter the host(s) and the headline guest (first in episode.guests) from
    // the People inspector — they already appear in the byline and the "About the
    // guest" card. Secondary guests should stay so multi-expert episodes don't lose
    // their named participants.
    const headlineGuest = (model.episode.guests || [])[0];
    const filterPeople = new Set([...(model.episode.hosts || []), headlineGuest].filter(Boolean));
    // Render known categories in curated priority order, then any extra
    // categories (paper, product, podcast, place, event, etc.) alphabetically.
    const knownSet = new Set(CAT_ORDER);
    const extraCats = Array.from(grouped.keys()).filter((c) => !knownSet.has(c)).sort();
    const orderedCats = [...CAT_ORDER, ...extraCats];
    orderedCats.forEach((cat) => {
      let items = grouped.get(cat) || [];
      if (cat === "person") items = items.filter((t) => !filterPeople.has(t.term));
      if (!items.length) return;
      const heading = CAT_LABELS[cat] || titleCaseCategory(cat);
      const list = items.map((t) => {
        const nameMarkup = t.url
          ? `<a class="name" href="${esc(t.url)}"${extAttrs(t.url)}>${esc(t.term)} <span class="ext" aria-hidden="true">↗</span></a>`
          : `<span class="name">${esc(t.term)}</span>`;
        return `
          <li class="entity" data-cat="${esc(cat)}">
            <span class="dot" aria-hidden="true"></span>
            <div>
              <div class="name-row">${nameMarkup}</div>
              ${t.notes ? `<div class="note">${esc(t.notes)}</div>` : ""}
            </div>
          </li>`;
      }).join("");
      sections.push(`
        <section class="card inspector-section">
          <h2 class="card-title">${esc(heading)} <span class="count">${items.length}</span></h2>
          <ul class="entity-list">${list}</ul>
        </section>`);
    });
    return sections.join("");
  }

  function personProfiles(model, names) {
    // Return an ordered list of {name, notes, url} for the given names,
    // using terminology entries for the bio + link, and falling back to
    // the raw name when the term isn't in terminology.
    if (!names || !names.length) return [];
    const termByName = new Map((model.terms || []).filter((t) => t.category === "person").map((t) => [t.term, t]));
    const out = [];
    for (const name of names) {
      const term = termByName.get(name);
      if (!term) {
        out.push({ name, notes: "", url: "" });
        continue;
      }
      const link = term.url || (model.links || []).find((l) => {
        const labelLower = (l.label || "").toLowerCase();
        return labelLower.includes(name.toLowerCase()) || labelLower.includes(name.split(" ")[0].toLowerCase());
      })?.url || "";
      out.push({ name: term.term, notes: term.notes || "", url: link });
    }
    return out;
  }

  function profileBlock(profile, isFirst) {
    return `
      ${isFirst ? "" : '<hr class="profile-divider">'}
      <p class="guest-name">${esc(profile.name)}</p>
      ${profile.notes ? `<p class="guest-bio">${esc(profile.notes)}</p>` : ""}
      ${profile.url ? `<a class="link" href="${esc(profile.url)}"${extAttrs(profile.url)}>${esc(profile.url.replace(/^https?:\/\//, "").replace(/\/$/, ""))} ↗</a>` : ""}`;
  }

  function profileCard(title, profiles) {
    if (!profiles.length) return "";
    const blocks = profiles.map((p, i) => profileBlock(p, i === 0)).join("");
    return `
      <section class="card">
        <h2 class="card-title">${esc(title)}</h2>
        ${blocks}
      </section>`;
  }

  function renderGlance(model) {
    const ep = model.episode;
    const durStr = fmtDuration(ep.duration_seconds);
    const dateStr = fmtDate(ep.published_at);

    const takeawayTiles = (model.takeaways || []).map((t, i) => `
      <article class="takeaway-tile">
        <div class="num">${String(i + 1).padStart(2, "0")}</div>
        <p>${esc(t)}</p>
      </article>`).join("");

    const claimCards = (model.claims || []).map((c) => `
      <article class="claim-card">
        <span class="topic-pill">${esc(c.topic)}</span>
        <p class="claim-text">${esc(c.claim)}</p>
        ${c.evidence ? `<p class="evidence"><strong>Evidence:</strong> ${esc(c.evidence)}</p>` : ""}
      </article>`).join("");

    const inspectorSections = renderInspectorSections(model);
    const hostProfiles = personProfiles(model, ep.hosts || []);
    const guestProfiles = personProfiles(model, ep.guests || []);
    const hostCards = profileCard(
      hostProfiles.length > 1 ? "About the hosts" : "About the host",
      hostProfiles
    );
    const guestCards = profileCard(
      guestProfiles.length > 1 ? "About the guests" : "About the guest",
      guestProfiles
    );

    const links = model.links || [];
    const nextLinks = links.filter((l) => l.url).slice(0, 6).map((l) => `
      <li>
        <a href="${esc(l.url)}"${extAttrs(l.url)}>
          <span>
            ${esc(l.label || l.url)}
            ${l.note ? `<span class="next-note">${esc(l.note)}</span>` : ""}
          </span>
          <span class="arrow">↗</span>
        </a>
      </li>`).join("");

    const bottomLine = model.bottom_line || ep.description || "";

    return `
      ${topbar(model, "glance")}
      <div class="studio">
        <aside class="col-episode">
          <section class="card episode-hero">
            <p class="kicker">${esc(ep.podcast_title)}${ep.episode_number ? ` · #${esc(ep.episode_number)}` : ""} · ${esc(durStr)}</p>
            <h1>${colorizeTitle(ep.short_title || ep.title)}</h1>
            <p class="summary">${esc(ep.description || bottomLine.slice(0, 240))}</p>
            <p class="meta-row">${esc([dateStr, (ep.hosts || []).join(", "), (ep.guests || []).join(", ")].filter(Boolean).join(" · "))}</p>
          </section>

          ${hostCards}
          ${guestCards}
        </aside>

        <main class="col-main">
          ${bottomLine ? `
            <section class="card thesis-card">
              <h2 class="card-title">The thesis</h2>
              <blockquote class="thesis-quote">${esc(bottomLine)}</blockquote>
            </section>` : ""}

          ${takeawayTiles ? `
            <section class="card">
              <h2 class="card-title">What to take away</h2>
              <div class="takeaway-grid">${takeawayTiles}</div>
            </section>` : ""}

          ${claimCards ? `
            <section class="card">
              <h2 class="card-title">Notable claims</h2>
              <div class="claim-list-studio">${claimCards}</div>
            </section>` : ""}
        </main>

        <aside class="col-inspector">
          ${inspectorSections}
          ${nextLinks ? `
            <section class="card inspector-section">
              <h2 class="card-title">Read next</h2>
              <ul class="next-list">${nextLinks}</ul>
            </section>` : ""}
        </aside>

        <footer class="studio-colophon">
          <span>Generated ${esc(model.generated_at)} · podcast-transformer</span>
          <a href="annotated-transcript.html">Read full transcript →</a>
        </footer>
      </div>`;
  }

  // ─── Transcript browser ───────────────────────────────────────────

  function renderTranscript(model) {
    const ep = model.episode;
    const hostSet = new Set(ep.hosts || []);
    const chapterByTurn = new Map();
    (model.chapters || []).forEach((c) => {
      const list = chapterByTurn.get(c.turn_id) || [];
      list.push(c);
      chapterByTurn.set(c.turn_id, list);
    });

    const body = [];
    (model.turns || []).forEach((turn) => {
      (chapterByTurn.get(turn.id) || []).forEach((c) => {
        body.push(`
          <section class="chapter-break" id="${esc(c.anchor_id)}">
            <h2>
              <time data-copy-anchor="${esc(c.anchor_id)}" data-timestamp="${esc(c.timestamp)}" title="Copy link to this chapter">${esc(c.timestamp)}</time>
              <span>${esc(c.title)}</span>
            </h2>
            ${c.summary ? `<p class="ch-summary">${esc(c.summary)}</p>` : ""}
          </section>`);
      });
      const kind = hostSet.has(turn.speaker) ? "host" : "guest";
      const speakerLabel = turn.show_speaker === false ? "" : `<span class="speaker">${esc(turn.speaker)}</span>`;
      body.push(`
        <article class="turn" id="${esc(turn.id)}" data-speaker="${esc(turn.speaker)}" data-speaker-kind="${kind}" data-search="${esc(`${turn.speaker} ${turn.text}`.toLowerCase())}">
          <p>${speakerLabel}${esc(turn.text)}</p>
        </article>`);
    });

    const dateStr = fmtDate(ep.published_at);
    const durStr = fmtDuration(ep.duration_seconds);
    const byline = [(ep.hosts || []).join(", "), (ep.guests || []).join(", ")].filter(Boolean).join(" with ");
    const metaLine = [dateStr, durStr, byline].filter(Boolean).join(" · ");
    const bottomLine = model.bottom_line || ep.description || "";

    return `
      ${topbar(model, "transcript")}
      <div class="t-layout">
        ${transcriptRail(model)}
        <main class="frame">
          <header class="header-block">
            <p class="kicker">${esc(ep.podcast_title)}${ep.episode_number ? ` · #${esc(ep.episode_number)}` : ""}</p>
            <h1 class="title">${esc(ep.short_title || ep.title)}</h1>
            <p class="meta">${esc(metaLine)}</p>
            ${bottomLine ? `<p class="lede">${esc(bottomLine)}</p>` : ""}
          </header>
          <section class="transcript-body" id="transcript-body">
            ${body.join("")}
          </section>
          <footer class="colophon">
            <span>${fmtNumber(model.stats.word_count)} words · ${model.stats.turn_count} turns · ${model.stats.chapter_count} chapters</span>
            <a href="podcast-at-a-glance.html">← Back to briefing</a>
          </footer>
        </main>
      </div>`;
  }

  // ─── Wiring ──────────────────────────────────────────────────────

  function wireJumpLinks(root) {
    root.addEventListener("click", (event) => {
      const link = event.target.closest("[data-jump-target]");
      if (link) {
        event.preventDefault();
        const target = qs(link.dataset.jumpTarget);
        if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
        const details = link.closest("details.jump");
        if (details) details.open = false;
        return;
      }
      const ts = event.target.closest("[data-copy-anchor]");
      if (ts) {
        const anchor = ts.dataset.copyAnchor;
        const stamp = ts.dataset.timestamp || "";
        const base = window.location.href.split("#")[0];
        copy(`${base}#${anchor} (${stamp})`);
        ts.classList.add("copied");
        setTimeout(() => ts.classList.remove("copied"), 900);
      }
    });
    document.addEventListener("click", (event) => {
      qsa("details.jump[open]", root).forEach((d) => {
        if (!d.contains(event.target)) d.open = false;
      });
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        qsa("details.jump[open]", root).forEach((d) => { d.open = false; });
      }
    });
  }

  function wireScrollSpy(root) {
    const breaks = qsa(".chapter-break[id]", root);
    const railItems = qsa(".chapter-nav-item[data-spy]", root);
    if (!breaks.length || !railItems.length) return;
    const itemById = new Map(railItems.map((el) => [el.dataset.spy, el]));
    const threshold = 140;
    let lastId = null;
    const update = () => {
      let active = breaks[0];
      for (const b of breaks) {
        if (b.getBoundingClientRect().top <= threshold) active = b;
        else break;
      }
      const id = active.id;
      if (id === lastId) return;
      lastId = id;
      railItems.forEach((el) => el.classList.toggle("active", el.dataset.spy === id));
      const activeItem = itemById.get(id);
      const rail = activeItem?.closest(".t-rail");
      if (activeItem && rail) {
        const r = activeItem.getBoundingClientRect();
        const rr = rail.getBoundingClientRect();
        if (r.top < rr.top + 40 || r.bottom > rr.bottom - 40) {
          activeItem.scrollIntoView({ block: "nearest" });
        }
      }
    };
    let raf = null;
    const onScroll = () => {
      if (raf) return;
      raf = requestAnimationFrame(() => { raf = null; update(); });
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    update();
  }

  function wireSearch(root, mode) {
    const input = qs("#global-search", root);
    if (!input) return;
    if (mode !== "transcript") return;
    const status = qs("#search-status", root);
    const turns = qsa(".turn", root);
    const escapeRegex = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    let allMarks = [];
    let currentIndex = -1;

    const markTurn = (turn, regex) => {
      const p = turn.querySelector("p");
      if (!p) return;
      const speakerSpan = p.querySelector(".speaker");
      if (!p.dataset.originalBody) {
        let body = "";
        let n = speakerSpan ? speakerSpan.nextSibling : p.firstChild;
        while (n) { body += n.textContent || ""; n = n.nextSibling; }
        p.dataset.originalBody = body;
      }
      let n = speakerSpan ? speakerSpan.nextSibling : p.firstChild;
      while (n) { const next = n.nextSibling; n.remove(); n = next; }
      const body = p.dataset.originalBody;
      if (!regex) {
        p.appendChild(document.createTextNode(body));
        return;
      }
      regex.lastIndex = 0;
      let m, last = 0;
      while ((m = regex.exec(body)) !== null) {
        if (m.index > last) p.appendChild(document.createTextNode(body.slice(last, m.index)));
        const mark = document.createElement("mark");
        mark.textContent = m[0];
        p.appendChild(mark);
        last = m.index + m[0].length;
        if (m.index === regex.lastIndex) regex.lastIndex++;
      }
      if (last < body.length) p.appendChild(document.createTextNode(body.slice(last)));
    };

    const nav = qs("#search-nav", root);
    const setCurrent = (index, { scroll = true } = {}) => {
      allMarks.forEach((m, i) => m.classList.toggle("current", i === index));
      currentIndex = index;
      if (status) {
        if (!input.value.trim()) status.textContent = "";
        else if (!allMarks.length) status.textContent = "no matches";
        else status.textContent = `${index + 1} / ${allMarks.length} · Enter or ← →`;
      }
      if (nav) nav.hidden = allMarks.length === 0;
      if (scroll && index >= 0 && allMarks[index]) {
        allMarks[index].scrollIntoView({ behavior: "smooth", block: "center" });
      }
    };

    const advance = (dir) => {
      if (!allMarks.length) return;
      const next = (currentIndex + dir + allMarks.length) % allMarks.length;
      setCurrent(next);
    };

    const runSearch = () => {
      const q = input.value.trim();
      const regex = q ? new RegExp(escapeRegex(q), "gi") : null;
      turns.forEach((t) => markTurn(t, regex));
      allMarks = qsa("mark", root);
      setCurrent(allMarks.length ? 0 : -1);
    };

    input.addEventListener("input", runSearch);
    input.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      if (!allMarks.length) return;
      event.preventDefault();
      advance(event.shiftKey ? -1 : 1);
    });
    if (nav) {
      nav.addEventListener("click", (event) => {
        const btn = event.target.closest("button");
        if (!btn) return;
        if (btn.hasAttribute("data-search-prev")) advance(-1);
        else if (btn.hasAttribute("data-search-next")) advance(1);
      });
    }
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && document.activeElement === input) {
        input.value = "";
        runSearch();
        input.blur();
        return;
      }
      // Left/Right arrows advance matches when the user isn't typing in a text field.
      if ((event.key === "ArrowRight" || event.key === "ArrowLeft") && allMarks.length) {
        const active = document.activeElement;
        if (active && (active.tagName === "INPUT" || active.tagName === "TEXTAREA" || active.isContentEditable)) return;
        event.preventDefault();
        advance(event.key === "ArrowRight" ? 1 : -1);
      }
    });
  }

  return {
    renderTranscriptBrowser(targetId) {
      const root = document.getElementById(targetId);
      root.innerHTML = renderTranscript(data());
      wireJumpLinks(root);
      wireSearch(root, "transcript");
      wireScrollSpy(root);
    },
    renderGlanceDashboard(targetId) {
      const root = document.getElementById(targetId);
      root.innerHTML = renderGlance(data());
      wireJumpLinks(root);
    },
  };
})();
