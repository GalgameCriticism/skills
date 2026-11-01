#!/usr/bin/env python3
"""把 article.json 渲染成仿 B 站专栏版式的本地 HTML 预览，供人工审核。

用法:
    python3 render_preview.py <期目录> [--article <article.json>] [--out <preview.html>]

图片使用相对路径引用，预览文件写在 <期目录>/_preview/ 下，用浏览器打开即可。
"""
from __future__ import annotations

import argparse
import html
import json
import os
from pathlib import Path
from safety import preview_directory

CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin: 0; background: #f1f2f3; font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif; color: #18191c; }
.wrap { max-width: 760px; margin: 0 auto; padding: 24px 16px 80px; }
.card { background: #fff; border-radius: 8px; padding: 28px 32px; box-shadow: 0 1px 4px rgba(0,0,0,.06); }
h1 { font-size: 22px; line-height: 1.5; margin: 0 0 4px; }
.badge { display: inline-block; font-size: 12px; padding: 1px 8px; border-radius: 4px; margin-left: 8px; vertical-align: middle; }
.badge.proposed { background: #fff3e6; color: #ff7f24; border: 1px solid #ffd591; }
.badge.manual { background: #f6ffed; color: #52c41a; border: 1px solid #b7eb8f; }
.intro { color: #9499a0; font-size: 14px; margin: 8px 0 16px; }
.cover { width: 100%; border-radius: 6px; display: block; margin: 4px 0 20px; }
h2 { font-size: 20px; font-weight: 700; margin: 28px 0 12px; padding-left: 10px; border-left: 4px solid #00aeec; }
h3 { font-size: 16px; font-weight: 700; margin: 20px 0 8px; }
p.body { font-size: 15px; line-height: 1.8; margin: 8px 0; color: #18191c; }
figure { margin: 12px 0; }
figure img { width: 100%; border-radius: 4px; display: block; }
figcaption { font-size: 12px; color: #9499a0; margin-top: 4px; word-break: break-all; }
.credits { margin-top: 36px; padding-top: 16px; border-top: 1px dashed #e3e5e7; font-size: 13px; color: #61666d; line-height: 1.9; }
.review { background: #fffbe6; border: 1px solid #ffe58f; border-radius: 8px; padding: 16px 20px; margin: 0 0 20px; font-size: 13px; }
.review h2 { border: none; padding: 0; margin: 0 0 8px; font-size: 15px; color: #ad6800; }
.review ul { margin: 6px 0; padding-left: 20px; }
.review li { margin: 3px 0; line-height: 1.6; }
.item-block { border: 1px solid transparent; }
.item-block:hover { border-color: #e3e5e7; }
.cands { font-size: 12px; color: #9499a0; margin: 4px 0 14px; line-height: 1.7; }
.cands .sel { color: #00aeec; font-weight: 600; }
.cands .unsel { color: #c9ccd0; text-decoration: line-through; }
.note { font-size: 12px; color: #ff7f24; }
"""


def esc(s: str) -> str:
    return html.escape(str(s), quote=True)


def render(data: dict, issue_dir: Path, article_path: Path) -> str:
    preview_dir = article_path.parent
    parts = []
    parts.append("<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>")
    parts.append("<meta name='viewport' content='width=device-width, initial-scale=1'>")
    parts.append("<title>预览 · %s</title>" % esc(data.get("title", "")))
    parts.append("<style>%s</style></head><body><div class='wrap'>" % CSS)

    # ---- 审核面板 ----
    warns = data.get("warnings", []) or []
    n_items = sum(len(s["items"]) for s in data.get("sections", []))
    n_pics = sum(len(i.get("selected", [])) for s in data.get("sections", []) for i in s["items"])
    review = ["<div class='review'><h2>人工审核清单</h2><ul>"]
    if data.get("title_status") == "proposed":
        review.append("<li>标题为脚本拟制（【Gal周报N期】+副标题），请人工定稿后把 <code>title_status</code> 改为 <code>manual</code>。</li>")
    if not data.get("credits"):
        review.append("<li><b>credits 为空</b>：请补充「文案：…」「文案审核：…」（参考上一期）。</li>")
    review.append(f"<li>共 {n_items} 条新闻、{n_pics} 张入选配图；灰显删除线的候选图为未选中，如需换图改 article.json 的 <code>selected</code>。</li>")
    review.append("<li>核对：章节顺序（新作→汉化→周边）、每条新闻的配图位置（首图在小标题后、次图在正文后）、正文无外链、无「（简）」前缀。</li>")
    if warns:
        review.append(f"<li>解析警告 {len(warns)} 条（多候选图默认取前 2 张等），见下方列表。</li>")
        review.append("<ul>" + "".join("<li class='note'>%s</li>" % esc(w) for w in warns) + "</ul>")
    review.append("</ul></div>")
    parts.append("".join(review))

    # ---- 文章卡片 ----
    parts.append("<div class='card'>")
    badge = (
        "<span class='badge proposed'>标题待定稿</span>"
        if data.get("title_status") == "proposed"
        else "<span class='badge manual'>标题已定稿</span>"
    )
    parts.append("<h1>%s%s</h1>" % (esc(data.get("title", "")), badge))
    if data.get("intro"):
        parts.append("<div class='intro'>%s</div>" % esc(data["intro"]))
    cover = data.get("source", {}).get("cover")
    if cover:
        parts.append("<img class='cover' src='%s' alt='cover'>" % esc(rel(cover, preview_dir, issue_dir)))
    for sec in data.get("sections", []):
        parts.append("<h2>%s</h2>" % esc(sec.get("heading", sec.get("key", ""))))
        for item in sec.get("items", []):
            sel = item.get("selected", [])
            parts.append("<div class='item-block'>")
            parts.append("<h3>%s</h3>" % esc(item.get("heading", "")))
            if sel:
                p = item["candidates"][sel[0]]
                parts.append("<figure><img src='%s' alt=''><figcaption>%s</figcaption></figure>" % (esc(rel(p, preview_dir, issue_dir)), esc(p)))
            for para in item.get("body", []):
                parts.append("<p class='body'>%s</p>" % esc(para))
            if len(sel) > 1:
                p = item["candidates"][sel[1]]
                parts.append("<figure><img src='%s' alt=''><figcaption>%s</figcaption></figure>" % (esc(rel(p, preview_dir, issue_dir)), esc(p)))
            # 候选图清单
            cands = item.get("candidates", [])
            if cands:
                chips = []
                for idx, c in enumerate(cands):
                    cls = "sel" if idx in sel else "unsel"
                    mark = "✓" if idx in sel else "✗"
                    chips.append("<span class='%s'>%s %s</span>" % (cls, mark, esc(Path(c).name)))
                parts.append("<div class='cands'>候选 %d 张：%s</div>" % (len(cands), " ".join(chips)))
            parts.append("</div>")
    for c in data.get("credits", []) or []:
        parts.append("<div class='credits'>%s</div>" % esc(c).replace("\n", "<br>"))
    parts.append("</div></div></body></html>")
    return "".join(parts)


def rel(p: str, preview_dir: Path, issue_dir: Path) -> str:
    """素材路径 → 相对预览文件所在目录的路径。"""
    src = (issue_dir / p) if not Path(p).is_absolute() else Path(p)
    try:
        return Path(os.path.relpath(src, preview_dir)).as_posix()
    except ValueError:
        return src.name


def main() -> None:
    ap = argparse.ArgumentParser(description="渲染 B 站图文版式预览 HTML")
    ap.add_argument("issue_dir", help="期目录")
    ap.add_argument("--article", help="article.json 路径（默认 <期目录>/_preview/article.json）")
    ap.add_argument("--out", help="输出 HTML（默认 <期目录>/_preview/preview.html）")
    args = ap.parse_args()

    issue_dir = Path(args.issue_dir).expanduser().resolve()
    preview = preview_directory(issue_dir)
    article_path = Path(args.article) if args.article else preview / "article.json"
    out = Path(args.out) if args.out else preview / "preview.html"
    data = json.loads(article_path.read_text(encoding="utf-8"))

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(data, issue_dir, article_path), encoding="utf-8")
    print(f"[preview] 已写出 {out}")
    print(f"[preview] 用浏览器打开该文件即可核对版式；改图请编辑 {article_path} 后重跑本脚本")


if __name__ == "__main__":
    main()
