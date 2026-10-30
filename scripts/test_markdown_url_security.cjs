const fs = require("fs");
const assert = require("assert");
const path = require("path");
const src = fs.readFileSync(path.join(__dirname, "..", "src", "gui",
   "screenshot_chat", "web_assets", "app.js"), "utf8");
const start = src.indexOf("function safeMarkdownHref(");
const end = src.indexOf("function renderAttachment(", start);
assert(start >= 0 && end > start, "markdown link functions are unavailable");
const funcs = new Function(src.slice(start, end) +
  "\nreturn {safeMarkdownHref,inlineMarkdown};")();
function escapeHtml(v) {
  return String(v).replaceAll("&", "&amp;").replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;").replaceAll('"', "&quot;");
}
const malicious = [
  "[x](javascript:alert(1))",
  "[x](JaVaScRiPt:alert(1))",
  "[x](java&#x73;cript:alert(1))",
  "[x](data:text/html,<svg/onload=alert(1)>)",
  '[x](https://valid.example/"onmouseover="alert(1))',
  "[x](vbscript:msgbox(1))",
  "[x](file:///C:/secret.txt)",
  "[x](//other.example/hi)",
];
for (const value of malicious) {
  const rendered = funcs.inlineMarkdown(escapeHtml(value));
  assert(!/<img\b|<script\b/i.test(rendered), value);
  assert(!/href\s*=\s*["']\s*(?:javascript|data|vbscript|file):/i.test(rendered), value);
  assert(!/\s(?:onmouseover|onerror|onclick)\s*=/i.test(rendered), value);
}
const safe = funcs.inlineMarkdown(escapeHtml("[doc](https://example.org/read)"));
assert(safe.includes('href="https://example.org/read"'), "normal links broken");
console.log("SnapArc markdown URL security: 9 cases passed");
