/* Tiny markdown renderer for the postmortem viewer.
 * Escape-first (all input HTML-escaped before any tags are added), supports
 * exactly what the postmortem template produces: headings, tables, lists,
 * checkboxes, fenced code, hr, bold/italic/inline code. ~100 lines, no deps.
 */
"use strict";

function renderMarkdown(src) {
  const esc = (s) => s
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");

  // Code spans are split out first so emphasis markers inside them stay
  // literal (odd indexes of the split are the captured `...` spans).
  const inline = (s) => esc(s)
    .split(/(`[^`]+`)/)
    .map((segment, idx) => idx % 2
      ? "<code>" + segment.slice(1, -1) + "</code>"
      : segment
          .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
          .replace(/(^|[\s(])\*([^*\n]+)\*/g, "$1<em>$2</em>")
          .replace(/(^|[\s(])_([^_\n]+)_/g, "$1<em>$2</em>"))
    .join("");

  const lines = src.split("\n");
  const out = [];
  let i = 0;
  let para = [];

  const flushPara = () => {
    if (para.length) {
      // Join before inline() so emphasis can span soft line breaks.
      out.push("<p>" + inline(para.join(" ")) + "</p>");
      para = [];
    }
  };

  while (i < lines.length) {
    const line = lines[i];

    if (/^```/.test(line)) {                        // fenced code block
      flushPara();
      const buf = [];
      i++;
      while (i < lines.length && !/^```/.test(lines[i])) buf.push(lines[i++]);
      i++;
      out.push("<pre><code>" + esc(buf.join("\n")) + "</code></pre>");
      continue;
    }

    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    if (heading) {
      flushPara();
      const level = heading[1].length;
      out.push(`<h${level}>` + inline(heading[2]) + `</h${level}>`);
      i++;
      continue;
    }

    if (/^(---|\*\*\*)\s*$/.test(line)) {           // horizontal rule
      flushPara();
      out.push("<hr>");
      i++;
      continue;
    }

    if (/^\|/.test(line) && /^\|[\s:|-]+\|?\s*$/.test(lines[i + 1] || "")) {
      flushPara();                                   // table
      const cells = (row) =>
        row.replace(/^\||\|$/g, "").split("|").map((c) => inline(c.trim()));
      const head = cells(line);
      i += 2;
      const rows = [];
      while (i < lines.length && /^\|/.test(lines[i])) rows.push(cells(lines[i++]));
      out.push(
        "<table><thead><tr>" +
        head.map((h) => `<th>${h}</th>`).join("") +
        "</tr></thead><tbody>" +
        rows.map((r) => "<tr>" + r.map((c) => `<td>${c}</td>`).join("") + "</tr>").join("") +
        "</tbody></table>"
      );
      continue;
    }

    if (/^\s*-\s+/.test(line)) {                     // unordered list (+ checkboxes)
      flushPara();
      const items = [];
      while (i < lines.length && /^\s*-\s+/.test(lines[i])) {
        let item = lines[i].replace(/^\s*-\s+/, "");
        let prefix = "";
        const box = item.match(/^\[([ xX])\]\s+(.*)$/);
        if (box) {
          prefix = `<input type="checkbox" disabled${box[1] !== " " ? " checked" : ""}> `;
          item = box[2];
        }
        items.push("<li>" + prefix + inline(item) + "</li>");
        i++;
      }
      out.push("<ul>" + items.join("") + "</ul>");
      continue;
    }

    if (line.trim() === "") {
      flushPara();
      i++;
      continue;
    }

    para.push(line.trim());
    i++;
  }
  flushPara();
  return out.join("\n");
}
