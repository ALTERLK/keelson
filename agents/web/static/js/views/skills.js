// Skill-evolution view: the two recorded lineages (one admitted, one
// quarantined) and whatever skills exist in the current workspace.

import { getJSON, isStatic } from "../api.js";
import { clear, fmtPct, h } from "../dom.js";
import { t } from "../i18n.js";
import { renderMarkdown } from "../markdown.js";

function diffView(text) {
  const lines = String(text || "").split("\n").filter((line, i, all) => !(i === all.length - 1 && line === ""));
  return h("pre", { class: "diff" }, lines.map((line) => {
    let kind = "";
    if (line.startsWith("+++") || line.startsWith("---")) kind = "file";
    else if (line.startsWith("@@")) kind = "hunk";
    else if (line.startsWith("+")) kind = "add";
    else if (line.startsWith("-")) kind = "del";
    return h("span", { class: `diff-line ${kind}` }, line || " ");
  }));
}

function gateTable(gate) {
  if (!gate || !gate.old || !gate.new) {
    return h("p", { class: "muted" }, gate?.reason || t("skills.gate.ungated"));
  }
  const regressed = gate.new.hard_failures > gate.old.hard_failures;
  const row = (label, a, b, worse) => h("tr", {}, h("th", {}, label), h("td", {}, a), h("td", { class: worse ? "bad" : "" }, b));
  return h("div", {},
    h("table", { class: "gate-table" },
      h("thead", {}, h("tr", {}, h("th", {}), h("th", {}, t("skills.gate.old")), h("th", {}, t("skills.gate.new")))),
      h("tbody", {},
        row(t("skills.gate.rules"), gate.old.rule_count, gate.new.rule_count, false),
        row(t("skills.gate.hard"), gate.old.hard_failures, gate.new.hard_failures, regressed),
        row(t("skills.gate.pass"), fmtPct(gate.old.pass_rate, 0), fmtPct(gate.new.pass_rate, 0), gate.new.pass_rate < gate.old.pass_rate),
      )),
    h("p", { class: "gate-verdict" },
      h("span", { class: `chip ${gate.action === "quarantine" ? "bad" : "ok"}` }, gate.action),
      " ", gate.reason || ""));
}

function stepBlock(step, index) {
  return h("div", { class: "evo-round" },
    h("div", { class: "evo-round-head" },
      h("span", { class: "evo-round-no" }, `${t("skills.round")} ${index + 1}`),
      h("code", {}, step.action),
      step.version ? h("span", { class: "muted" }, `→ v${step.version}`) : null),
    h("div", { class: "evo-steps" },
      h("div", { class: "evo-step" }, h("h4", {}, t("skills.step.evidence")), h("blockquote", {}, step.evidence || "—")),
      h("div", { class: "evo-step" }, h("h4", {}, t("skills.step.candidate")),
        h("div", { class: "prose small", html: renderMarkdown(step.candidate.instructions || "—") })),
      h("div", { class: "evo-step" }, h("h4", {}, t("skills.step.decision")),
        h("p", {}, h("code", {}, step.decision.action || "—"), " ", step.decision.reason || "")),
      h("div", { class: "evo-step" }, h("h4", {}, t("skills.step.gate")), gateTable(step.gate)),
    ));
}

function lineageCard(lineage) {
  const rejected = lineage.outcome === "quarantine";
  return h("section", { class: `card lineage ${rejected ? "rejected" : "admitted"}` },
    h("div", { class: "lineage-head" },
      h("h2", {}, h("code", {}, lineage.skill)),
      h("span", { class: `chip ${rejected ? "bad" : "ok"}` }, t(`skills.outcome.${lineage.outcome}`))),
    lineage.steps.map(stepBlock),
    h("h3", {}, t(rejected ? "skills.diff.quarantine" : "skills.diff.admit")),
    diffView(lineage.diff),
    lineage.card
      ? h("details", { class: "card-doc" }, h("summary", {}, t("skills.card")),
          h("div", { class: "prose small", html: renderMarkdown(lineage.card) }))
      : null,
  );
}

function liveSkills(skills) {
  return h("section", { class: "card" },
    h("h2", {}, t("skills.live.title")),
    skills.length
      ? h("ul", { class: "skill-list" }, skills.map((skill) =>
          h("li", {},
            h("div", { class: "skill-name" }, h("code", {}, skill.name),
              h("span", { class: "chip" }, skill.source),
              skill.history.length ? h("span", { class: "muted" }, `${skill.history.length + 1} ${t("skills.live.versions")}`) : null),
            h("div", { class: "muted" }, skill.description))))
      : h("p", { class: "muted" }, t("skills.live.empty")));
}

export function mount(root) {
  const page = h("div", { class: "page" }, h("p", { class: "muted" }, t("common.loading")));
  root.append(page);
  let alive = true;

  Promise.all([getJSON("evolution"), isStatic ? [] : getJSON("skills").catch(() => [])]).then(([lineages, skills]) => {
    if (!alive) return;
    clear(page).append(
      h("header", { class: "page-head" }, h("h1", {}, t("skills.title")), h("p", {}, t("skills.lead"))),
      ...lineages.map(lineageCard),
      ...(isStatic ? [] : [liveSkills(skills)]),
    );
  }).catch((error) => {
    if (alive) clear(page).append(h("p", { class: "notice error" }, `${t("common.error")}: ${error.message}`));
  });

  return () => { alive = false; };
}
