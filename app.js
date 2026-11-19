(function () {
  "use strict";

  const SITE_TITLE = (window.SITE && window.SITE.title) || "";

  if (typeof window.applySiteBranding === "function") window.applySiteBranding();

  const THEME_KEY = "schwartz-spot-theme";
  const TIP_KEY = "schwartz-spot-install-tip-dismissed";
  const SECTION_ORDER = [
    "ai",
    "news",
    "x",
    "politics",
    "economy",
    "sports",
    "movies",
    "tv",
    "media",
    "music",
  ];
  const SECTION_TITLES = {
    ai: "AI",
    news: "News",
    x: "On X",
    politics: "Politics",
    economy: "Economy",
    sports: "Sports",
    movies: "Movies",
    tv: "TV",
    media: "Media",
    music: "Music",
  };

  const main = document.getElementById("main");
  const dateline = document.getElementById("dateline");
  const themeToggle = document.getElementById("themeToggle");
  const themeColorMeta = document.getElementById("themeColorMeta");
  const installTip = document.getElementById("installTip");
  const dismissTip = document.getElementById("dismissTip");
  const archiveNav = document.getElementById("archiveNav");
  const sectionNav = document.getElementById("sectionNav");
  const sectionNavScroller = document.getElementById("sectionNavScroller");
  const updatedStamp = document.getElementById("updatedStamp");

  const REFRESH_MS = 3 * 60 * 1000;
  let lastIndexUpdatedAt = "";
  let lastDayUpdatedAt = "";
  let lastFetchAt = 0;
  let refreshTimer = null;

  const LIGHT_THEME = "#f4efe6";
  const DARK_THEME = "#141311";

  let sectionObserver = null;

  function prefersDark() {
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  }

  function currentTheme() {
    const saved = localStorage.getItem(THEME_KEY);
    if (saved === "light" || saved === "dark") return saved;
    return prefersDark() ? "dark" : "light";
  }

  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    themeColorMeta.content = theme === "dark" ? DARK_THEME : LIGHT_THEME;
    themeToggle.setAttribute(
      "aria-label",
      theme === "dark" ? "Switch to light mode" : "Switch to dark mode"
    );
  }

  applyTheme(currentTheme());

  themeToggle.addEventListener("click", function () {
    const next = currentTheme() === "dark" ? "light" : "dark";
    localStorage.setItem(THEME_KEY, next);
    applyTheme(next);
  });

  if (window.matchMedia) {
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", function () {
      if (!localStorage.getItem(THEME_KEY)) applyTheme(currentTheme());
    });
  }

  function isStandalone() {
    return (
      window.matchMedia("(display-mode: standalone)").matches ||
      window.navigator.standalone === true
    );
  }

  function maybeShowInstallTip() {
    if (isStandalone()) return;
    if (localStorage.getItem(TIP_KEY) === "1") return;
    installTip.hidden = false;
  }

  dismissTip.addEventListener("click", function () {
    localStorage.setItem(TIP_KEY, "1");
    installTip.hidden = true;
  });

  maybeShowInstallTip();

  function parseRoute() {
    const hash = (location.hash || "#/").replace(/^#/, "");
    const path = hash.replace(/^\/+/, "");
    if (!path || path === "today") return { view: "day", date: null };
    if (path === "archive") return { view: "archive" };
    if (/^\d{4}-\d{2}-\d{2}$/.test(path)) return { view: "day", date: path };
    return { view: "day", date: null };
  }

  function formatDate(iso) {
    const parts = iso.split("-").map(Number);
    const d = new Date(parts[0], parts[1] - 1, parts[2]);
    return d.toLocaleDateString("en-US", {
      weekday: "long",
      year: "numeric",
      month: "long",
      day: "numeric",
    });
  }

  function formatUpdated(iso) {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return "";
    const time = d.toLocaleTimeString("en-US", {
      timeZone: "America/New_York",
      hour: "numeric",
      minute: "2-digit",
    });
    return "Updated " + time + " ET";
  }

  function setUpdatedStamp(iso) {
    if (!updatedStamp) return;
    const label = iso ? formatUpdated(iso) : "";
    if (!label) {
      updatedStamp.hidden = true;
      updatedStamp.textContent = "";
      return;
    }
    updatedStamp.hidden = false;
    updatedStamp.textContent = label;
    if (iso) updatedStamp.setAttribute("datetime", iso);
    else updatedStamp.removeAttribute("datetime");
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function textOrBlank(value) {
    if (value == null) return "";
    return String(value).trim();
  }

  function normalizeSections(day) {
    const byId = {};
    const incoming = Array.isArray(day.sections) ? day.sections : [];
    incoming.forEach(function (sec) {
      if (!sec || !sec.id) return;
      const stories = Array.isArray(sec.stories) ? sec.stories : [];
      const posts = Array.isArray(sec.posts) ? sec.posts : [];
      if (!stories.length && !posts.length) return;
      byId[sec.id] = {
        id: sec.id,
        title: sec.title || SECTION_TITLES[sec.id] || sec.id,
        stories: stories,
        posts: posts,
      };
    });
    if (!incoming.length && Array.isArray(day.stories) && day.stories.length) {
      byId.ai = { id: "ai", title: "AI", stories: day.stories };
    }
    return SECTION_ORDER.map(function (id) {
      return byId[id];
    }).filter(Boolean);
  }

  async function fetchJson(path) {
    const res = await fetch(path, { cache: "no-store" });
    if (!res.ok) throw new Error("Could not load " + path + " (" + res.status + ")");
    return res.json();
  }

  async function loadIndex() {
    const data = await fetchJson("data/index.json");
    const dates = Array.isArray(data) ? data : data.dates || [];
    return dates
      .map(function (item) {
        if (typeof item === "string") return { date: item };
        return item;
      })
      .filter(function (item) {
        return item && /^\d{4}-\d{2}-\d{2}$/.test(item.date);
      })
      .sort(function (a, b) {
        return a.date < b.date ? 1 : a.date > b.date ? -1 : 0;
      });
  }

  function renderStories(stories) {
    if (!stories || !stories.length) {
      return '<p class="empty">No stories were recorded in this section.</p>';
    }
    return stories
      .map(function (story) {
        const headline = textOrBlank(story.headline) || "Untitled story";
        const summary = textOrBlank(story.summary);
        const source = textOrBlank(story.source);
        const url = textOrBlank(story.url);
        return (
          '<article class="story">' +
          "<h3>" +
          escapeHtml(headline) +
          "</h3>" +
          (summary ? '<p class="summary">' + escapeHtml(summary) + "</p>" : "") +
          '<p class="meta">' +
          (source ? "<span>" + escapeHtml(source) + "</span>" : "") +
          (url
            ? '<a href="' +
              escapeHtml(url) +
              '" rel="noopener noreferrer" target="_blank">Read the story</a>'
            : "") +
          "</p>" +
          "</article>"
        );
      })
      .join("");
  }

  function formatPostTime(iso) {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return "";
    return (
      d.toLocaleString("en-US", {
        timeZone: "America/New_York",
        weekday: "short",
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
      }) + " ET"
    );
  }

  function verifiedBadge(post) {
    if (!post || !post.verified) return "";
    const label = post.verified_type === "business" ? "Verified organization" : "Verified";
    return (
      '<span class="x-badge" title="' +
      escapeHtml(label) +
      '"><svg viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M8 1.2l1.65 1.2 1.95.15.15 1.95L13.8 8l-1.2 1.65-.15 1.95-1.95.15L8 14.8l-1.65-1.2-1.95-.15-.15-1.95L2.2 8l1.2-1.65.15-1.95 1.95-.15L8 1.2zm-.35 8.55l3.4-3.4-.9-.9-2.5 2.5-1.15-1.15-.9.9 2.05 2.05z"/></svg><span class="visually-hidden">' +
      escapeHtml(label) +
      "</span></span>"
    );
  }

  function renderPosts(posts) {
    if (!posts || !posts.length) {
      return '<p class="empty">No posts were recorded in this section.</p>';
    }
    return posts
      .map(function (post) {
        const name = textOrBlank(post.author_name) || "Unknown";
        const handle = textOrBlank(post.author_handle);
        const text = post.text == null ? "" : String(post.text);
        const url = textOrBlank(post.url);
        const time = formatPostTime(post.created_at);
        const datetime = textOrBlank(post.created_at);
        const escapedText = escapeHtml(text).replace(/\n/g, "<br>");
        return (
          '<article class="x-post">' +
          '<header class="x-post-head">' +
          '<div class="x-author">' +
          '<span class="x-name">' +
          escapeHtml(name) +
          "</span>" +
          verifiedBadge(post) +
          (handle
            ? '<span class="x-handle">@' + escapeHtml(handle.replace(/^@/, "")) + "</span>"
            : "") +
          "</div>" +
          (time
            ? "<time" +
              (datetime ? ' datetime="' + escapeHtml(datetime) + '"' : "") +
              ">" +
              escapeHtml(time) +
              "</time>"
            : "") +
          "</header>" +
          '<p class="x-text is-clamped">' +
          escapedText +
          "</p>" +
          '<button type="button" class="x-expand" hidden>Show more</button>' +
          (url
            ? '<p class="meta"><a href="' +
              escapeHtml(url) +
              '" rel="noopener noreferrer" target="_blank">View on X</a></p>'
            : "") +
          "</article>"
        );
      })
      .join("");
  }

  function bindPostExpand(root) {
    if (!root) return;
    root.querySelectorAll(".x-post").forEach(function (card) {
      const text = card.querySelector(".x-text");
      const btn = card.querySelector(".x-expand");
      if (!text || !btn) return;
      if (text.scrollHeight > text.clientHeight + 2) {
        btn.hidden = false;
        btn.addEventListener("click", function () {
          const open = text.classList.toggle("is-clamped");
          // toggle returns true if class is now present (clamped)
          btn.textContent = open ? "Show more" : "Show less";
        });
      }
    });
  }

  function hideSectionNav() {

    if (sectionObserver) {
      sectionObserver.disconnect();
      sectionObserver = null;
    }
    sectionNav.hidden = true;
    sectionNavScroller.innerHTML = "";
  }

  function setActiveChip(id) {
    const chips = sectionNavScroller.querySelectorAll(".chip");
    chips.forEach(function (chip) {
      const on = chip.getAttribute("data-section") === id;
      chip.classList.toggle("is-active", on);
      if (on) chip.setAttribute("aria-current", "true");
      else chip.removeAttribute("aria-current");
    });
    const active = sectionNavScroller.querySelector(".chip.is-active");
    if (active && active.scrollIntoView) {
      active.scrollIntoView({ inline: "center", block: "nearest", behavior: "smooth" });
    }
  }

  function bindSectionNav(sections) {
    hideSectionNav();
    if (!sections.length) return;

    sections.forEach(function (sec, i) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "chip" + (i === 0 ? " is-active" : "");
      btn.setAttribute("data-section", sec.id);
      if (i === 0) btn.setAttribute("aria-current", "true");
      btn.textContent = sec.title;
      btn.addEventListener("click", function () {
        const el = document.getElementById("section-" + sec.id);
        if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
        setActiveChip(sec.id);
      });
      sectionNavScroller.appendChild(btn);
    });
    sectionNav.hidden = false;

    if (!("IntersectionObserver" in window)) return;
    sectionObserver = new IntersectionObserver(
      function (entries) {
        const visible = entries
          .filter(function (e) {
            return e.isIntersecting;
          })
          .sort(function (a, b) {
            return a.boundingClientRect.top - b.boundingClientRect.top;
          });
        if (!visible.length) return;
        const id = visible[0].target.getAttribute("data-section");
        if (id) setActiveChip(id);
      },
      { root: null, rootMargin: "-30% 0px -55% 0px", threshold: [0, 0.25, 0.6] }
    );
    sections.forEach(function (sec) {
      const el = document.getElementById("section-" + sec.id);
      if (el) sectionObserver.observe(el);
    });
  }

  function sectionCountLabel(sec) {
    const posts = sec.posts && sec.posts.length;
    const stories = sec.stories && sec.stories.length;
    if (posts && !stories) return posts + (posts === 1 ? " post" : " posts");
    if (stories && !posts) return stories + (stories === 1 ? " story" : " stories");
    const bits = [];
    if (stories) bits.push(stories + (stories === 1 ? " story" : " stories"));
    if (posts) bits.push(posts + (posts === 1 ? " post" : " posts"));
    return bits.join(" · ") || "0 stories";
  }

  function sectionBody(sec) {
    let html = "";
    if (sec.posts && sec.posts.length) html += renderPosts(sec.posts);
    if (sec.stories && sec.stories.length) html += renderStories(sec.stories);
    if (!html) html = '<p class="empty">Nothing was recorded in this section.</p>';
    return html;
  }

  function renderDay(day, isLatest) {
    const dateLabel = formatDate(day.date);
    const sections = normalizeSections(day);
    const total = sections.reduce(function (n, sec) {
      return n + (sec.stories ? sec.stories.length : 0) + (sec.posts ? sec.posts.length : 0);
    }, 0);
    document.title = (isLatest ? "Today · " : "") + dateLabel + " · " + SITE_TITLE;
    dateline.textContent = dateLabel;
    setUpdatedStamp(day.updatedAt);
    lastDayUpdatedAt = day.updatedAt || "";

    if (!sections.length) {
      hideSectionNav();
      main.innerHTML = '<p class="empty">No stories were recorded for this day.</p>';
      return;
    }

    main.innerHTML = sections
      .map(function (sec) {
        return (
          '<section class="section" id="section-' +
          escapeHtml(sec.id) +
          '" data-section="' +
          escapeHtml(sec.id) +
          '" aria-labelledby="heading-' +
          escapeHtml(sec.id) +
          '">' +
          '<div class="section-head">' +
          '<h2 id="heading-' +
          escapeHtml(sec.id) +
          '">' +
          escapeHtml(sec.title) +
          "</h2>" +
          '<span class="count">' +
          escapeHtml(sectionCountLabel(sec)) +
          "</span>" +
          "</div>" +
          sectionBody(sec) +
          "</section>"
        );
      })
      .join("") +
      (isLatest ? "" : '<p><a class="back-link" href="#/">Back to latest</a></p>');

    bindSectionNav(sections);
    bindPostExpand(main);
    if (!isLatest) {
      // keep nav on older days too when more than one section exists
    }
  }

  function renderArchive(index, latestDate) {
    hideSectionNav();
    document.title = "Archive · " + SITE_TITLE;
    dateline.textContent = "Archive";
    setUpdatedStamp("");
    if (!index.length) {
      main.innerHTML = '<p class="empty">No brief days have been added yet.</p>';
      return;
    }
    const items = index
      .map(function (item) {
        const stories = typeof item.storyCount === "number" ? item.storyCount : null;
        const posts = typeof item.postCount === "number" ? item.postCount : null;
        const bits = [];
        if (stories != null) bits.push(stories + (stories === 1 ? " story" : " stories"));
        if (posts) bits.push(posts + (posts === 1 ? " post" : " posts"));
        if (item.date === latestDate) bits.unshift("Latest");
        return (
          "<li>" +
          '<a href="#/' +
          escapeHtml(item.date) +
          '">' +
          '<span class="archive-date">' +
          escapeHtml(formatDate(item.date)) +
          "</span>" +
          (bits.length ? '<span class="archive-meta">' + escapeHtml(bits.join(" · ")) + "</span>" : "") +
          "</a>" +
          "</li>"
        );
      })
      .join("");
    main.innerHTML =
      '<section class="section" aria-labelledby="archive-heading">' +
      '<div class="section-head"><h2 id="archive-heading">Earlier editions</h2></div>' +
      '<ul class="archive-list">' +
      items +
      "</ul>" +
      '<p><a class="back-link" href="#/">Back to latest</a></p>' +
      "</section>";
  }

  async function render() {
    const route = parseRoute();
    archiveNav.setAttribute("aria-current", route.view === "archive" ? "page" : "false");
    hideSectionNav();
    main.innerHTML = '<p class="status">Loading…</p>';
    try {
      const index = await loadIndex();
      if (!index.length) {
        dateline.textContent = SITE_TITLE;
        setUpdatedStamp("");
        main.innerHTML = '<p class="empty">No editions have been published yet.</p>';
        return;
      }
      const latestDate = index[0].date;
      lastIndexUpdatedAt = index[0].updatedAt || lastIndexUpdatedAt;
      if (route.view === "archive") {
        renderArchive(index, latestDate);
        return;
      }
      const date = route.date || latestDate;
      const known = index.some(function (item) {
        return item.date === date;
      });
      if (!known) {
        dateline.textContent = formatDate(date);
        setUpdatedStamp("");
        main.innerHTML =
          '<p class="error">No edition is on file for ' +
          escapeHtml(formatDate(date)) +
          '.</p><p><a class="back-link" href="#/archive">Browse the archive</a></p>';
        return;
      }
      const day = await fetchJson("data/" + date + ".json");
      day.date = day.date || date;
      renderDay(day, date === latestDate);
    } catch (err) {
      hideSectionNav();
      dateline.textContent = SITE_TITLE;
      setUpdatedStamp("");
      main.innerHTML =
        '<p class="error">This edition could not be loaded. If you are offline, try opening a day you have already read.</p>';
      console.error(err);
    }
  }

  window.addEventListener("hashchange", render);
  render();

  async function refreshIfChanged() {
    try {
      const index = await loadIndex();
      if (!index.length) return;
      const latestDate = index[0].date;
      const indexUpdated = index[0].updatedAt || "";
      const route = parseRoute();
      const viewingLatest =
        route.view === "day" && (!route.date || route.date === latestDate);
      if (!viewingLatest) {
        lastIndexUpdatedAt = indexUpdated;
        return;
      }
      const day = await fetchJson("data/" + latestDate + ".json");
      day.date = day.date || latestDate;
      const dayUpdated = day.updatedAt || "";
      if (indexUpdated === lastIndexUpdatedAt && dayUpdated === lastDayUpdatedAt) {
        return;
      }
      lastIndexUpdatedAt = indexUpdated;
      renderDay(day, true);
    } catch (err) {
      console.warn("Live refresh failed:", err);
    }
  }

  function scheduleRefresh() {
    if (refreshTimer) clearInterval(refreshTimer);
    refreshTimer = setInterval(function () {
      lastFetchAt = Date.now();
      refreshIfChanged();
    }, REFRESH_MS);
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState !== "visible") return;
      if (Date.now() - lastFetchAt < 60 * 1000) return;
      lastFetchAt = Date.now();
      refreshIfChanged();
    });
  }

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("sw.js", { scope: "./" }).catch(function (err) {
      console.warn("Service worker was not registered:", err);
    });
  }

  scheduleRefresh();
})();
