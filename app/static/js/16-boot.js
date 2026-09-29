    // ── Boot: initial page render + data load ───────────────────────────
    // Must be the LAST script file: loadPageData dispatches to loaders
    // declared across all the other js/ files, and function hoisting does
    // not cross <script> boundaries.
    {
      const initialPage = (location.hash || '#dashboard').slice(1);
      showPage(initialPage);
      // The page's data loads once the user is known (userReady, from
      // 10-forms-fx-bank.js): the role decides which page is shown — a
      // manager deep-linked to #dashboard lands on Expenses — and the
      // dashboard used to be fetched for roles that can't open it. Loaded
      // once, for the page actually shown.
      userReady.then(() => loadPageData(activePage() || initialPage));
    }
