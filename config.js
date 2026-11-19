/* Display title and short mark. Change these when a new title is chosen.
   Also update name, short_name, and description in manifest.json, plus
   mentions in README.md and scripts/add_day.py — those files cannot
   read this config. */
window.SITE = {
  title: "The Schwartz Spot",
  shortName: "Schwartz Spot",
  shortMark: "ΣΣ",
  description:
    "Dave Schwartz’s personal news desk: AI, news, politics, economy, sports, movies, TV, media, and music.",
};

window.applySiteBranding = function () {
  var site = window.SITE;
  if (!site || !site.title) return;
  document.title = site.title;
  var apple = document.querySelector('meta[name="apple-mobile-web-app-title"]');
  if (apple) apple.setAttribute("content", site.shortName || site.title);
  var desc = document.querySelector('meta[name="description"]');
  if (desc && site.description) desc.setAttribute("content", site.description);
  var mark = document.getElementById("siteHome");
  if (mark) {
    mark.setAttribute("aria-label", site.title + " home");
    mark.textContent = site.shortMark || "";
  }
  var nameplate = document.getElementById("siteNameplate");
  if (nameplate) nameplate.textContent = site.title;
};
