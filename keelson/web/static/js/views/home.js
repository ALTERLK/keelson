// Overview page: what Keelson is, how one turn works, and where to start.

import { getJSON, isStatic } from "../api.js";
import { features, hero, pick, problems, stages, steps } from "../content.js";
import { clear, h, svg } from "../dom.js";
import { t } from "../i18n.js";
import { loadReplay, session } from "../session.js";

const REPO = "https://github.com/ALTERLK/keelson";

async function startTour() {
  const trace = session.traces[0];
  if (!trace) { location.hash = "#/console"; return; }
  await loadReplay(trace.id, { guided: true });
  location.hash = "#/console";
}

// The five stages laid out on a ring; the last one closes the loop back to the model.
function loopDiagram(selected, onSelect) {
  const size = 320, width = 460, cx = width / 2, cy = size / 2, radius = 108;
  const point = (index) => {
    const angle = (-90 + index * (360 / stages.length)) * (Math.PI / 180);
    return [cx + radius * Math.cos(angle), cy + radius * Math.sin(angle)];
  };
  const root = svg("svg", { viewBox: `0 0 ${width} ${size}`, class: "loop-ring", role: "group", "aria-label": t("home.loop") });
  root.append(svg("circle", { cx, cy, r: radius, class: "loop-ring-track" }));
  root.append(svg("circle", { cx, cy, r: radius, class: "loop-ring-flow" }));
  stages.forEach((stage, index) => {
    const [x, y] = point(index);
    const active = stage.id === selected;
    const node = svg("g", {
      class: "loop-ring-node" + (active ? " active" : ""), tabindex: 0, role: "button",
      "aria-pressed": active ? "true" : "false", "aria-label": pick(stage.name),
    },
      svg("circle", { cx: x, cy: y, r: 30, class: "loop-ring-hit" }),
      svg("circle", { cx: x, cy: y, r: active ? 13 : 9, class: "loop-ring-dot" }),
      svg("text", {
        x: x + (Math.abs(x - cx) < 8 ? 0 : x > cx ? 22 : -22), y: y + (Math.abs(x - cx) < 8 ? -24 : y > cy ? 30 : 5),
        class: "loop-ring-label", "text-anchor": Math.abs(x - cx) < 8 ? "middle" : (y > cy ? "middle" : x > cx ? "start" : "end"),
      }, pick(stage.name)),
    );
    node.addEventListener("click", () => onSelect(stage.id));
    node.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onSelect(stage.id); } });
    root.append(node);
  });
  root.append(
    svg("text", { x: cx, y: cy - 4, class: "loop-ring-center", "text-anchor": "middle" }, String(stages.findIndex((s) => s.id === selected) + 1).padStart(2, "0")),
    svg("text", { x: cx, y: cy + 18, class: "loop-ring-sub", "text-anchor": "middle" }, `/ ${String(stages.length).padStart(2, "0")}`),
  );
  return root;
}

function setupCard(checks) {
  if (isStatic) {
    return h("section", { class: "card setup" },
      h("h2", {}, t("home.static.title")),
      h("p", { class: "card-note" }, t("home.static.body")),
      h("pre", { class: "code" }, `git clone ${REPO}.git\ncd keelson\nstart.bat        # Windows\n./start.sh       # macOS / Linux`),
    );
  }
  const hasKey = checks.some((check) => check.id === "key" && check.status === "ok");
  return h("section", { class: "card setup" },
    h("h2", {}, t("home.setup.title")),
    h("p", { class: "card-note" }, t(session.server.live ? "home.setup.live" : hasKey ? "home.setup.replayFlag" : "home.setup.replay")),
    checks.length
      ? h("ul", { class: "checks" }, checks.map((check) =>
          h("li", { class: check.status },
            h("span", { class: "check-mark" }, check.status === "ok" ? "✓" : check.status === "warn" ? "!" : "✗"),
            h("div", {},
              h("div", {}, h("strong", {}, t(`doctor.${check.id}`, check.label)), " ", h("span", { class: "muted" }, check.detail)),
              check.fix ? h("div", { class: "check-fix" }, check.fix) : null))))
      : null,
    session.server.live || hasKey ? null
      : h("pre", { class: "code" }, `keelson init      # ${t("home.setup.init")}\nkeelson doctor    # ${t("home.setup.doctor")}`),
  );
}

export function mount(root) {
  const page = h("div", { class: "page home" });
  root.append(page);
  let alive = true;
  let selected = "gate";
  let checks = [];

  function render() {
    const stage = stages.find((s) => s.id === selected);
    clear(page).append(
      h("header", { class: "hero" },
        h("div", { class: "eyebrow" }, pick(hero.eyebrow)),
        h("h1", {}, pick(hero.title)),
        h("p", { class: "hero-lead" }, pick(hero.lead)),
        h("div", { class: "hero-actions" },
          h("button", { type: "button", class: "btn primary large", onclick: startTour }, pick(hero.primary)),
          isStatic
            ? h("a", { class: "btn large", href: REPO, target: "_blank", rel: "noopener" }, pick(hero.secondaryStatic))
            : h("a", { class: "btn large", href: "#/console" }, pick(hero.secondaryLive))),
      ),

      h("section", {},
        h("h2", { class: "section-title" }, t("home.problems")),
        h("div", { class: "cards-3" }, problems.map((problem, index) =>
          h("article", { class: "card problem" },
            h("span", { class: "problem-no" }, String(index + 1).padStart(2, "0")),
            h("h3", {}, pick(problem.title)),
            h("p", {}, pick(problem.body)))))),

      h("section", {},
        h("h2", { class: "section-title" }, t("home.loop")),
        h("div", { class: "card loop-card" },
          loopDiagram(selected, (id) => { selected = id; render(); }),
          h("div", { class: "loop-detail" },
            h("p", { class: "muted small" }, t("home.loop.hint")),
            h("h3", {}, pick(stage.name)),
            h("p", { class: "loop-plain" }, pick(stage.plain)),
            h("p", { class: "card-note" }, pick(stage.detail)),
            h("div", { class: "loop-code" }, h("span", { class: "muted" }, t("home.loop.code")), h("code", {}, stage.code))))),

      h("section", {},
        h("h2", { class: "section-title" }, t("home.steps")),
        h("div", { class: "split-2" },
          h("ol", { class: "steps" }, steps.map((step, index) =>
            h("li", {}, h("span", { class: "step-no" }, index + 1),
              h("div", {}, h("h3", {}, pick(step.title)), h("p", {}, pick(step.body)))))),
          setupCard(checks))),

      h("section", {},
        h("h2", { class: "section-title" }, t("home.features")),
        h("div", { class: "feature-grid" }, features.map((feature) =>
          h("article", { class: "feature" },
            h("h3", {}, pick(feature.name)),
            h("p", {}, pick(feature.what)),
            h("p", { class: "feature-how" }, h("span", { class: "muted" }, t("home.features.how") + " "), pick(feature.how)))))),
    );
  }

  render();
  if (!isStatic) {
    getJSON("doctor").then((result) => { if (alive) { checks = result.checks; render(); } }).catch(() => {});
  }
  return () => { alive = false; };
}
