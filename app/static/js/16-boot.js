    // ── Boot: initial page render + data load ───────────────────────────
    // Must be the LAST script file: loadPageData dispatches to loaders
    // declared across all the other js/ files, and function hoisting does
    // not cross <script> boundaries.
    {
      const initialPage = (location.hash || '#dashboard').slice(1);
      showPage(initialPage);
      loadPageData(initialPage);
    }
