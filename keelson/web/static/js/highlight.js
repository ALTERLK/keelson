// A small syntax highlighter for the languages that show up in agent output.
//
// It is a single regular expression per language, tried left to right: at each
// position the first alternative that matches wins, so a "#" inside a string is
// part of the string and never starts a comment. Text is escaped as it is
// emitted, so the result is safe to assign to innerHTML.

import { escapeHtml } from "./dom.js";

const PY_KEYWORDS = "and|as|assert|async|await|break|class|continue|def|del|elif|else|except|finally|for|from|global|if|import|in|is|lambda|nonlocal|not|or|pass|raise|return|try|while|with|yield|None|True|False|self";
const JS_KEYWORDS = "async|await|break|case|catch|class|const|continue|default|delete|do|else|export|extends|finally|for|from|function|if|import|in|instanceof|let|new|null|of|return|static|switch|this|throw|true|false|try|typeof|undefined|var|void|while|yield";
const SH_KEYWORDS = "if|then|else|elif|fi|for|in|do|done|while|case|esac|function|return|export|cd|echo|set";

function grammar(comment, keywords) {
  return new RegExp(
    [
      `(?<comment>${comment})`,
      `(?<string>"""[\\s\\S]*?"""|'''[\\s\\S]*?'''|"(?:\\\\.|[^"\\\\\\n])*"|'(?:\\\\.|[^'\\\\\\n])*'|\`(?:\\\\.|[^\`\\\\])*\`)`,
      `(?<number>\\b\\d+(?:\\.\\d+)?\\b)`,
      `(?<keyword>\\b(?:${keywords})\\b)`,
      `(?<func>\\b[A-Za-z_][\\w]*(?=\\())`,
    ].join("|"),
    "g",
  );
}

const grammars = {
  python: grammar("#[^\\n]*", PY_KEYWORDS),
  js: grammar("\\/\\/[^\\n]*|\\/\\*[\\s\\S]*?\\*\\/", JS_KEYWORDS),
  bash: grammar("#[^\\n]*", SH_KEYWORDS),
  json: /(?<func>"(?:\\.|[^"\\\n])*"(?=\s*:))|(?<string>"(?:\\.|[^"\\\n])*")|(?<number>-?\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b)|(?<keyword>\b(?:true|false|null)\b)/g,
};

const aliases = {
  py: "python", python: "python", python3: "python",
  js: "js", javascript: "js", ts: "js", typescript: "js", jsx: "js", tsx: "js",
  sh: "bash", bash: "bash", shell: "bash", zsh: "bash", powershell: "bash", cmd: "bash", console: "bash",
  json: "json", jsonl: "json",
};

export function languageOf(nameOrPath) {
  const key = String(nameOrPath || "").toLowerCase().split(".").pop();
  return aliases[key] || null;
}

export function highlight(code, language) {
  const pattern = grammars[aliases[String(language || "").toLowerCase()] || language];
  if (!pattern) return escapeHtml(code);
  pattern.lastIndex = 0;
  let out = "";
  let last = 0;
  for (const match of code.matchAll(pattern)) {
    out += escapeHtml(code.slice(last, match.index));
    const kind = Object.keys(match.groups).find((name) => match.groups[name] !== undefined);
    out += `<span class="syn-${kind}">${escapeHtml(match[0])}</span>`;
    last = match.index + match[0].length;
  }
  return out + escapeHtml(code.slice(last));
}
