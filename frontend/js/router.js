const ROUTES = new Set(["/", "/strategy", "/adaptive", "/history", "/settings"]);

export function currentRoute() {
  return ROUTES.has(location.pathname) ? location.pathname : "/";
}

export function installRouter(render) {
  document.addEventListener("click", (event) => {
    const link = event.target.closest("a[data-route]");
    if (!link) return;
    event.preventDefault();
    history.pushState({}, "", link.href);
    render(currentRoute());
  });
  addEventListener("popstate", () => render(currentRoute()));
}
