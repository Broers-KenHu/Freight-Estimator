from __future__ import annotations

import argparse
import datetime as dt
import html
import os
import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SKILL_DIR = Path.home() / ".codex" / "skills" / "md2html"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "docs" / "html"


LABELS = {
    "en": {
        "toc": "Contents",
        "rec": "Recommended",
        "print": "Print / Save PDF",
        "theme": "Toggle theme",
        "close": "Close",
        "skip": "Skip to content",
        "source": "Source:",
        "read": "~{minutes} min read",
        "brand": {
            "PLAN": "Plan",
            "SPEC": "Spec",
            "SYSTEM DESIGN": "System Design",
            "RFC": "RFC",
            "RUNBOOK": "Runbook",
            "POSTMORTEM": "Postmortem",
            "BRAINSTORM": "Brainstorm",
            "NOTES": "Notes",
        },
        "callout": {
            "info": "Context",
            "warn": "Heads up",
            "danger": "Do not do this",
            "success": "Done",
            "decision": "Decision",
            "tip": "Tip",
        },
    },
    "zh": {
        "toc": "目录",
        "rec": "推荐",
        "print": "打印 / 保存 PDF",
        "theme": "切换主题",
        "close": "关闭",
        "skip": "跳到正文",
        "source": "来源:",
        "read": "~{minutes} 分钟阅读",
        "brand": {
            "PLAN": "计划",
            "SPEC": "规格",
            "SYSTEM DESIGN": "系统设计",
            "RFC": "RFC",
            "RUNBOOK": "操作手册",
            "POSTMORTEM": "复盘",
            "BRAINSTORM": "头脑风暴",
            "NOTES": "笔记",
        },
        "callout": {
            "info": "背景",
            "warn": "注意",
            "danger": "禁止操作",
            "success": "已完成",
            "decision": "决定",
            "tip": "提示",
        },
    },
}


def detect_language(text: str) -> str:
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    ascii_words = len(re.findall(r"\b[A-Za-z]{3,}\b", text))
    return "zh" if cjk > max(12, ascii_words // 5) else "en"


def estimate_minutes(text: str, lang: str) -> int:
    if lang == "zh":
        units = len(re.findall(r"[\u4e00-\u9fff]", text)) / 450
    else:
        units = len(re.findall(r"\b\w+\b", text)) / 250
    return max(1, round(units))


def slugify(text: str, used: set[str]) -> str:
    raw = re.sub(r"<[^>]+>", "", text).strip().lower()
    raw = re.sub(r"[`*_~\[\]():/\\.,;!?|\"']", " ", raw)
    raw = re.sub(r"\s+", "-", raw, flags=re.UNICODE).strip("-")
    raw = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff_-]+", "", raw)
    slug = raw or "section"
    base = slug
    counter = 2
    while slug in used:
        slug = f"{base}-{counter}"
        counter += 1
    used.add(slug)
    return slug


def strip_markdown(text: str) -> str:
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"[*_#>|-]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def inline_md(text: str) -> str:
    code_values: list[str] = []

    def take_code(match: re.Match[str]) -> str:
        code_values.append(f"<code>{html.escape(match.group(1))}</code>")
        return f"\u0000CODE{len(code_values) - 1}\u0000"

    text = re.sub(r"`([^`]+)`", take_code, text)
    text = html.escape(text)
    text = re.sub(
        r"!\[([^\]]*)\]\(([^)]+)\)",
        lambda m: f'<img src="{html.escape(m.group(2), quote=True)}" alt="{m.group(1)}">',
        text,
    )
    text = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        lambda m: f'<a href="{html.escape(m.group(2), quote=True)}">{m.group(1)}</a>',
        text,
    )
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", text)
    for idx, value in enumerate(code_values):
        text = text.replace(f"\u0000CODE{idx}\u0000", value)
    return text


def parse_table(lines: list[str]) -> str:
    rows = []
    for line in lines:
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        rows.append(cells)
    if len(rows) < 2:
        return ""
    headers = rows[0]
    body = rows[2:] if re.match(r"^\s*:?-{3,}:?\s*$", rows[1][0]) else rows[1:]
    out = ["<div class=\"table-wrap\">", "<table>", "<thead><tr>"]
    out.extend(f"<th>{inline_md(cell)}</th>" for cell in headers)
    out.append("</tr></thead><tbody>")
    for row in body:
        padded = row + [""] * (len(headers) - len(row))
        out.append("<tr>")
        out.extend(f"<td>{inline_md(cell)}</td>" for cell in padded[: len(headers)])
        out.append("</tr>")
    out.append("</tbody></table></div>")
    return "\n".join(out)


def callout(kind: str, title: str, body: str) -> str:
    icon = {
        "info": "i-info",
        "warn": "i-warn",
        "danger": "i-danger",
        "success": "i-success",
        "decision": "i-decision",
        "tip": "i-tip",
    }.get(kind, "i-info")
    return (
        f'<aside class="callout callout-{kind}">\n'
        f'  <svg class="callout-icon" viewBox="0 0 24 24" aria-hidden="true"><use href="#{icon}"/></svg>\n'
        f'  <div class="callout-body">\n'
        f'    <p class="callout-title">{html.escape(title)}</p>\n'
        f'    <p>{inline_md(body)}</p>\n'
        f"  </div>\n"
        f"</aside>"
    )


def flush_paragraph(buffer: list[str], out: list[str]) -> None:
    if not buffer:
        return
    paragraph = " ".join(line.strip() for line in buffer).strip()
    if paragraph:
        out.append(f"<p>{inline_md(paragraph)}</p>")
    buffer.clear()


def markdown_body(markdown: str, lang: str) -> tuple[str, list[tuple[int, str, str]], str, str]:
    lines = markdown.splitlines()
    first_h1 = ""
    subtitle = ""
    body: list[str] = []
    toc: list[tuple[int, str, str]] = []
    used: set[str] = set()
    para: list[str] = []
    in_code = False
    code_lang = ""
    code_lines: list[str] = []
    skipped_h1 = False
    i = 0
    after_h1 = False

    while i < len(lines):
        line = lines[i]

        fence = re.match(r"^\s*```([\w+-]*)\s*$", line)
        if fence and not in_code:
            flush_paragraph(para, body)
            in_code = True
            code_lang = fence.group(1).lower()
            code_lines = []
            i += 1
            continue
        if fence and in_code:
            code = "\n".join(code_lines)
            if code_lang == "mermaid":
                body.append(
                    '<figure class="diagram">\n'
                    f'  <pre class="mermaid">\n{html.escape(code)}\n  </pre>\n'
                    '  <figcaption class="diagram-caption">Mermaid diagram from source document.</figcaption>\n'
                    '</figure>'
                )
            else:
                lang_class = f' class="language-{html.escape(code_lang)}"' if code_lang else ""
                body.append(f"<pre><code{lang_class}>{html.escape(code)}</code></pre>")
            in_code = False
            i += 1
            continue
        if in_code:
            code_lines.append(line)
            i += 1
            continue

        heading = re.match(r"^(#{1,4})\s+(.+?)\s*$", line)
        if heading:
            flush_paragraph(para, body)
            level = len(heading.group(1))
            text = strip_markdown(heading.group(2))
            if level == 1 and not skipped_h1:
                first_h1 = text
                skipped_h1 = True
                after_h1 = True
                i += 1
                continue
            if level <= 3:
                html_level = max(2, level)
                ident = slugify(text, used)
                toc.append((html_level, ident, text))
                body.append(f'<h{html_level} id="{ident}">{inline_md(text)}</h{html_level}>')
            else:
                body.append(f"<h4>{inline_md(text)}</h4>")
            i += 1
            continue

        if after_h1 and not subtitle and line.strip() and not line.lstrip().startswith("#"):
            subtitle = strip_markdown(line)
            after_h1 = False

        if not line.strip():
            flush_paragraph(para, body)
            i += 1
            continue

        if line.strip().startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-{3,}", lines[i + 1]):
            flush_paragraph(para, body)
            table_lines = [line]
            i += 1
            while i < len(lines) and lines[i].strip().startswith("|"):
                table_lines.append(lines[i])
                i += 1
            body.append(parse_table(table_lines))
            continue

        quote = re.match(r"^\s*>\s?(.*)", line)
        if quote:
            flush_paragraph(para, body)
            quote_lines = [quote.group(1)]
            i += 1
            while i < len(lines):
                m = re.match(r"^\s*>\s?(.*)", lines[i])
                if not m:
                    break
                quote_lines.append(m.group(1))
                i += 1
            text = " ".join(quote_lines).strip()
            lowered = text.lower()
            if lowered.startswith(("note:", "important:", "context:")):
                body.append(callout("info", LABELS[lang]["callout"]["info"], re.sub(r"^[^:]+:\s*", "", text)))
            elif lowered.startswith(("warning:", "risk:", "caution:")):
                body.append(callout("warn", LABELS[lang]["callout"]["warn"], re.sub(r"^[^:]+:\s*", "", text)))
            else:
                body.append(f"<blockquote>{inline_md(text)}</blockquote>")
            continue

        if re.match(r"^\s*[-*+]\s+", line):
            flush_paragraph(para, body)
            items: list[str] = []
            while i < len(lines):
                m = re.match(r"^\s*[-*+]\s+(.*)", lines[i])
                if not m:
                    break
                item = m.group(1)
                task = re.match(r"\[( |x|X)\]\s+(.*)", item)
                if task:
                    checked = " checked" if task.group(1).lower() == "x" else ""
                    items.append(
                        f'<li class="task-list-item"><input type="checkbox" class="task-list-item-checkbox" disabled{checked}> {inline_md(task.group(2))}</li>'
                    )
                else:
                    items.append(f"<li>{inline_md(item)}</li>")
                i += 1
            body.append("<ul>\n" + "\n".join(items) + "\n</ul>")
            continue

        if re.match(r"^\s*\d+\.\s+", line):
            flush_paragraph(para, body)
            items = []
            while i < len(lines):
                m = re.match(r"^\s*\d+\.\s+(.*)", lines[i])
                if not m:
                    break
                items.append(f"<li>{inline_md(m.group(1))}</li>")
                i += 1
            body.append("<ol>\n" + "\n".join(items) + "\n</ol>")
            continue

        image = re.match(r"^\s*!\[([^\]]*)\]\(([^)]+)\)\s*$", line)
        if image:
            flush_paragraph(para, body)
            alt = html.escape(image.group(1), quote=True)
            src = html.escape(image.group(2), quote=True)
            body.append(f'<figure><img src="{src}" alt="{alt}"><figcaption>{alt}</figcaption></figure>')
            i += 1
            continue

        if re.match(r"^\s*---+\s*$", line):
            flush_paragraph(para, body)
            body.append("<hr>")
            i += 1
            continue

        para.append(line)
        i += 1

    flush_paragraph(para, body)
    if in_code:
        body.append(f"<pre><code>{html.escape(chr(10).join(code_lines))}</code></pre>")
    if not toc:
        ident = slugify("content", used)
        toc.append((2, ident, "Content" if lang == "en" else "内容"))
        body.insert(0, f'<h2 id="{ident}">{toc[0][2]}</h2>')
    return "\n\n".join(body), toc, first_h1, subtitle


def doc_type_for(path: Path, text: str) -> str:
    probe = f"{path.name} {text[:500]}".lower()
    if "postmortem" in probe or "复盘" in probe:
        return "POSTMORTEM"
    if "runbook" in probe or "guide" in probe or "setup" in probe or "操作" in probe:
        return "RUNBOOK"
    if "plan" in probe or "方案" in probe or "计划" in probe:
        return "PLAN"
    if "architecture" in probe or "design" in probe or "设计" in probe or "schema" in probe:
        return "SYSTEM DESIGN"
    if "test" in probe or "e2e" in probe or "spec" in probe or "测试" in probe:
        return "SPEC"
    if "rfc" in probe:
        return "RFC"
    return "NOTES"


def replace_between(template: str, start: str, end: str, replacement: str) -> str:
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.DOTALL)
    new_content = start + "\n\n" + replacement + "\n\n" + end
    return pattern.sub(lambda _match: new_content, template)


def render_html(source: Path, out_path: Path, template: str, project_root: Path) -> dict[str, object]:
    markdown = source.read_text(encoding="utf-8", errors="replace")
    lang = detect_language(markdown)
    labels = LABELS[lang]
    content, toc, h1, subtitle = markdown_body(markdown, lang)
    title = h1 or source.stem.replace("_", " ").replace("-", " ").title()
    subtitle = subtitle or (strip_markdown(markdown).split(". ")[0][:180] if markdown.strip() else source.name)
    doc_type = doc_type_for(source, markdown)
    minutes = estimate_minutes(markdown, lang)
    rel_source = source.relative_to(project_root).as_posix()
    toc_html = "\n".join(
        f'<a href="#{ident}" class="lvl-{level}">{html.escape(text)}</a>' for level, ident, text in toc
    )
    rendered = template
    replacements = {
        "{{LANG}}": lang,
        "{{REC_LABEL}}": labels["rec"],
        "{{TITLE}}": html.escape(title),
        "{{SUBTITLE}}": html.escape(subtitle[:220]),
        "{{DOC_TYPE}}": doc_type,
        "{{SOURCE_FILE}}": html.escape(rel_source),
        "{{DATE}}": dt.date.today().isoformat(),
        "{{READ_TIME}}": labels["read"].format(minutes=minutes),
        "{{BRAND_LABEL}}": labels["brand"][doc_type],
        "{{TOC_TITLE}}": labels["toc"],
        "{{PRINT_TOOLTIP}}": labels["print"],
        "{{THEME_TOOLTIP}}": labels["theme"],
        "{{CLOSE_LABEL}}": labels["close"],
        "{{SKIP_LINK_LABEL}}": labels["skip"],
        "{{FOOTER_NOTE}}": f'{labels["source"]} {html.escape(rel_source)}',
    }
    for key, value in replacements.items():
        rendered = rendered.replace(key, value)
    rendered = rendered.replace("<!-- TOC_ENTRIES -->", toc_html)
    rendered = replace_between(rendered, "<!-- CONTENT_START -->", "<!-- CONTENT_END -->", content)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(rendered, encoding="utf-8")
    return {"source": source, "output": out_path, "title": title, "sections": len(toc), "minutes": minutes}


def collect_sources(project_root: Path) -> list[Path]:
    roots = [
        project_root / "README.md",
        project_root / "AGENTS.md",
        project_root / "frontend" / "README.md",
    ]
    roots.extend(sorted((project_root / "docs").rglob("*.md")))
    roots.extend(sorted((project_root / "reports").rglob("*.md")))
    seen: set[Path] = set()
    out: list[Path] = []
    for path in roots:
        if not path.exists() or path in seen:
            continue
        parts = {p.lower() for p in path.parts}
        if {"html", "node_modules", ".pytest_cache", "artifacts", ".venv", "venv"} & parts:
            continue
        seen.add(path)
        out.append(path)
    return out


def output_path_for(source: Path, project_root: Path, output_dir: Path) -> Path:
    rel = source.relative_to(project_root)
    return output_dir / rel.with_suffix(".html")


def render_index(results: list[dict[str, object]], output_dir: Path) -> Path:
    rows = []
    for item in sorted(results, key=lambda x: str(x["source"])):
        out_path = Path(item["output"])
        source = Path(item["source"])
        href = os.path.relpath(out_path, output_dir).replace("\\", "/")
        rows.append(
            "| "
            + " | ".join(
                [
                    f"[{source.relative_to(PROJECT_ROOT).as_posix()}]({href})",
                    str(item["sections"]),
                    f"~{item['minutes']} min",
                ]
            )
            + " |"
        )
    index_md = "\n".join(
        [
            "# Freight Intelligence HTML Documents",
            "",
            "项目 Markdown 文档已转换为可浏览 HTML。点击下方链接即可阅读，页面支持目录、深色模式、打印/保存 PDF 和代码复制。",
            "",
            "| Source | Sections | Read time |",
            "|---|---:|---:|",
            *rows,
            "",
        ]
    )
    template = (DEFAULT_SKILL_DIR / "template.html").read_text(encoding="utf-8")
    temp_md = output_dir / "_index_source.md"
    temp_md.write_text(index_md, encoding="utf-8")
    index_path = output_dir / "index.html"
    render_html(temp_md, index_path, template, output_dir)
    temp_md.unlink(missing_ok=True)
    return index_path


def verify_html(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    problems = []
    if "{{" in text or "}}" in text:
        problems.append("leftover placeholder")
    ids = set(re.findall(r'id="([^"]+)"', text))
    hrefs = re.findall(r'href="#([^"]+)"', text)
    missing = sorted({href for href in hrefs if href and href not in ids and not href.startswith("i-")})
    if missing:
        problems.append("missing anchors: " + ", ".join(missing[:5]))
    if len(re.findall(r"<script\b", text)) != 2:
        problems.append("unexpected script count")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skill-dir", type=Path, default=DEFAULT_SKILL_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    template_path = args.skill_dir / "template.html"
    if not template_path.exists():
        raise SystemExit(f"template.html not found: {template_path}")
    template = template_path.read_text(encoding="utf-8")

    sources = collect_sources(PROJECT_ROOT)
    results = []
    problems = []
    for source in sources:
        out_path = output_path_for(source, PROJECT_ROOT, args.output_dir)
        result = render_html(source, out_path, template, PROJECT_ROOT)
        results.append(result)
        for problem in verify_html(out_path):
            problems.append(f"{out_path}: {problem}")

    index_path = render_index(results, args.output_dir)
    for problem in verify_html(index_path):
        problems.append(f"{index_path}: {problem}")

    print(f"Converted {len(results)} Markdown files")
    print(f"Index: {index_path}")
    if problems:
        print("Verification warnings:")
        for problem in problems:
            print(f"- {problem}")
        return 1
    print("Verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
