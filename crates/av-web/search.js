(() => {
  function escapeHtml(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function resultRow(result) {
    const provider = result.provider ? result.provider + " / " : "";
    return `
      <a class="av-search-result package-row" href="${escapeHtml(result.url)}">
        <span>${escapeHtml(result.title)}</span>
        <small>${escapeHtml(provider + (result.summary || result.packageKey || ""))}</small>
      </a>`;
  }

  function init(root) {
    const locale = root.dataset.locale || "en";
    const endpoint = root.dataset.searchEndpoint || "/pkg/search.json";
    const placeholder = root.dataset.placeholder || "Search packages";
    root.innerHTML = `
      <form class="av-search-form" role="search">
        <input class="av-search-input" type="search" name="q" autocomplete="off" placeholder="${escapeHtml(placeholder)}" aria-label="${escapeHtml(placeholder)}">
      </form>
      <div class="av-search-status" aria-live="polite"></div>
      <div class="av-search-results"></div>`;

    const form = root.querySelector("form");
    const input = root.querySelector("input");
    const status = root.querySelector(".av-search-status");
    const results = root.querySelector(".av-search-results");
    let activeController = null;

    async function search() {
      const query = input.value.trim();
      if (!query) {
        status.textContent = "";
        results.innerHTML = "";
        return;
      }
      if (activeController) activeController.abort();
      activeController = new AbortController();
      const params = new URLSearchParams({ q: query, locale, limit: "8" });
      status.textContent = "Searching...";
      try {
        const response = await fetch(`${endpoint}?${params}`, {
          signal: activeController.signal,
          headers: { Accept: "application/json" }
        });
        if (!response.ok) throw new Error(`search failed: ${response.status}`);
        const data = await response.json();
        const count = Number(data.totalCount || 0);
        status.textContent = count === 1 ? "1 result" : `${count} results`;
        results.innerHTML = (data.results || []).map(resultRow).join("");
      } catch (error) {
        if (error.name === "AbortError") return;
        status.textContent = "Search unavailable";
        results.innerHTML = "";
      }
    }

    form.addEventListener("submit", event => {
      event.preventDefault();
      search();
    });
    input.addEventListener("input", () => {
      window.clearTimeout(input._avSearchTimer);
      input._avSearchTimer = window.setTimeout(search, 160);
    });

    function focusFromHash() {
      if (window.location.hash === "#search") {
        window.requestAnimationFrame(() => {
          root.scrollIntoView({ block: "center" });
          input.focus({ preventScroll: true });
        });
      }
    }
    window.addEventListener("hashchange", focusFromHash);
    focusFromHash();
  }

  window.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("[data-av-package-search]").forEach(init);
  });
})();
