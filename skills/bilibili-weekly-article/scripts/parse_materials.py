#!/usr/bin/env python3
"""解析期目录素材（docx 文字稿 + 图包），生成 article.json。

用法:
    python3 parse_materials.py <期目录> [--output <article.json 路径>] [--readlist rl471774]

素材约定（见 references/material-contract.md）:
    期目录/
      *去注音*.docx             # 文字稿——必须选标题带「去注音」的版本（原始稿含
                                 # 【注音】括号，会漏进正文）；无此版时告警并回退
      *文章封面*.png           # 图文横幅封面（可选）
      图包/{新作,汉化,周边}/{N}_{标题}/…   # 配图，一闻一目录

输出 article.json 字段见 SKILL.md「素材契约」一节。只读素材，不写期目录内除
--output 指定之外的任何文件。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
import zipfile
from difflib import SequenceMatcher
from pathlib import Path
from safety import preview_directory
from xml.etree import ElementTree as ET

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

SECTION_DIR_TO_HEADING = {
    "新作": "新作资讯",
    "汉化": "汉化情报",
    "周边": "周边信息",
}
# docx 中章节标题段的样式 id（Word 中文环境常见取值）；同时用文本匹配兜底
SECTION_STYLE_IDS = {"2", "3", "1"}
ITEM_STYLE_IDS = {"3", "4"}
INTRO_TEMPLATE = "本文首发于公众号 Galgame批评"
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def norm_text(s: str) -> str:
    """标题归一化：NFKD 分解（合并゛等组合记号）、去空白、注音前缀。"""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("\u3000", " ").strip()
    s = re.sub(r"^（简）\s*", "", s)
    s = re.sub(r"\s+", "", s)
    return s


def is_url_paragraph(s: str) -> bool:
    return bool(re.fullmatch(r"(https?://\S+\s*)+", s.strip()))


def strip_trailing_urls(s: str) -> str:
    """去掉正文末尾粘贴的来源链接（发布版不含外链）。"""
    return re.sub(r"\s*https?://\S+\s*$", "", s).strip()


def read_docx_paragraphs(docx_path: Path) -> list[dict]:
    """返回 [{style, text, bold}]，仅含非空段。"""
    with zipfile.ZipFile(docx_path) as z:
        xml = z.read("word/document.xml")
    root = ET.fromstring(xml)
    paras = []
    for p in root.iter(W + "p"):
        texts, bold = [], False
        for r in p.iter(W + "r"):
            rpr = r.find(W + "rPr")
            if rpr is not None and rpr.find(W + "b") is not None:
                bold = True
            for t in r.iter(W + "t"):
                texts.append(t.text or "")
        line = "".join(texts)
        if not line.strip():
            continue
        ppr = p.find(W + "pPr")
        style = ""
        if ppr is not None:
            st = ppr.find(W + "pStyle")
            if st is not None:
                style = st.get(W + "val") or ""
        paras.append({"style": style, "text": line.strip(), "bold": bold})
    return paras


def find_docx(issue_dir: Path) -> Path | None:
    cands = [
        p
        for p in sorted(issue_dir.glob("*.docx"))
        if not p.name.startswith("~$") and not p.name.startswith(".")
    ]
    for p in cands:
        if "去注音" in p.name:
            return p
    for p in cands:
        if re.search(r"\d+\s*期", p.name):
            return p
    return cands[0] if cands else None


def find_cover(issue_dir: Path) -> Path | None:
    for p in sorted(issue_dir.glob("*.png")):
        if "文章封面" in p.name:
            return p
    for p in sorted(issue_dir.glob("*.png")):
        if "封面" in p.name:
            return p
    return None


def scan_image_pack(pack_root: Path) -> dict[str, list[dict]]:
    """图包 → {章节key: [{n, title, dir, images[]}]}，按目录名前 N_ 排序。"""
    out: dict[str, list[dict]] = {}
    if not pack_root.is_dir():
        return out
    for sec_dir in sorted(p for p in pack_root.iterdir() if p.is_dir()):
        items = []
        for item_dir in sorted(
            (p for p in sec_dir.iterdir() if p.is_dir()),
            key=lambda p: (int(m.group(1)) if (m := re.match(r"(\d+)_", p.name)) else 999, p.name),
        ):
            m = re.match(r"(\d+)_(.*)", item_dir.name)
            n = int(m.group(1)) if m else None
            title = m.group(2) if m else item_dir.name
            images = sorted(
                p for p in item_dir.iterdir()
                if p.is_file() and p.suffix.lower() in IMAGE_EXTS
            )
            if images:
                items.append({"n": n, "title": title, "dir": item_dir, "images": images})
        if items:
            out[sec_dir.name] = items
    return out


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, norm_text(a), norm_text(b)).ratio()


def parse(issue_dir: Path, readlist: str | None) -> dict:
    warnings: list[str] = []
    docx = find_docx(issue_dir)
    if docx is None:
        raise SystemExit(f"[parse] 期目录内未找到 docx 文字稿: {issue_dir}")
    # 选档强调：排版必须用标题带「去注音」的版本。回退到原始稿时告警，
    # 并在正文里检测【注音】残留（原始稿形如 なのたろ【Nanotaro】）。
    if "去注音" not in docx.name:
        warnings.append(
            f"选用的是含注音原始稿「{docx.name}」（目录内无标题带『去注音』的 docx）："
            "正文大概率残留【注音】括号，请向制作方要去注音版，或人工清除后再排版"
        )
    cover = find_cover(issue_dir)
    pack = scan_image_pack(issue_dir / "图包")

    paras = read_docx_paragraphs(docx)

    # 注音残留检测：正文中出现【…】短括号（排除章节/小标题常见的书名号场景）
    ruby_hits: list[str] = []
    for p in paras:
        for m in re.finditer(r"【[^】\n]{1,40}】", p["text"]):
            ruby_hits.append(m.group(0))
    if ruby_hits:
        warnings.append(
            "正文检出疑似【注音】残留 "
            + "、".join(ruby_hits[:3])
            + ("…" if len(ruby_hits) > 3 else "")
            + "：确认使用的是标题带『去注音』的 docx，或人工清除"
        )

    # 头部：第一段=期号+周期，第二段=副标题
    m = re.match(r"\s*(\d+)\s*期\s*(.*)", paras[0]["text"]) if paras else None
    issue_no = int(m.group(1)) if m else None
    period = m.group(2).strip() if m else ""
    if issue_no is None:
        warnings.append(f"未能从首段解析期号: {paras[0]['text'] if paras else '(空)'}")
    subtitle = paras[1]["text"] if len(paras) > 1 else ""
    body_start = 2

    # 章节切分：样式命中或文本命中图包章节名
    sections: list[dict] = []
    cur_sec = None
    cur_item = None

    def close_item():
        nonlocal cur_item
        if cur_sec is not None and cur_item is not None:
            cur_item["body"] = [
                strip_trailing_urls(b) for b in cur_item.pop("_body") if not is_url_paragraph(b)
            ]
            cur_item["body"] = [b for b in cur_item["body"] if b]
            cur_sec["items"].append(cur_item)
        cur_item = None

    def close_section():
        nonlocal cur_sec
        close_item()
        if cur_sec is not None and cur_sec["items"]:
            sections.append(cur_sec)
        cur_sec = None

    sec_keys = list(pack.keys()) or list(SECTION_DIR_TO_HEADING.keys())

    for para in paras[body_start:]:
        text, style = para["text"], para["style"]
        nkey = norm_text(text)
        # 章节标题
        if nkey in sec_keys or (style in SECTION_STYLE_IDS and nkey in SECTION_DIR_TO_HEADING):
            close_section()
            key = nkey if nkey in sec_keys else nkey
            cur_sec = {
                "key": key,
                "heading": SECTION_DIR_TO_HEADING.get(key, text),
                "items": [],
            }
            continue
        if cur_sec is None:
            # 章节前的杂段（如 credits 之外的说明）先缓存到 warnings
            warnings.append(f"章节外段落已忽略: {text[:40]}")
            continue
        # 新闻小标题：样式/加粗判定；先收尾上一条，再按序号+相似度匹配图包目录
        pack_items = pack.get(cur_sec["key"], [])
        looks_like_heading = style in ITEM_STYLE_IDS or para["bold"]
        if looks_like_heading:
            close_item()
            matched = None
            expect_n = len(cur_sec["items"]) + 1
            for it in pack_items:
                if it["n"] == expect_n:
                    matched = it
                    break
            if matched is None:
                best, best_r = None, 0.0
                for it in pack_items:
                    r = similarity(text, it["title"])
                    if r > best_r:
                        best, best_r = it, r
                if best is not None and best_r >= 0.5:
                    matched = best
            if matched is not None:
                ratio = similarity(text, matched["title"])
                if ratio < 0.9:
                    warnings.append(
                        f"[{cur_sec['key']}/{matched['n']}] 标题与图包目录不完全一致 "
                        f"(docx: {text[:30]} | 图包: {matched['title'][:30]})"
                    )
                n_images = len(matched["images"])
                selected = [0, 1] if n_images >= 2 else ([0] if n_images == 1 else [])
                if n_images > 2:
                    warnings.append(
                        f"[{cur_sec['key']}/{matched['n']}] 图包共 {n_images} 张，"
                        f"默认取前 2 张，请在审核时确认"
                    )
                cur_item = {
                    "n": matched["n"],
                    "heading": re.sub(r"^（简）\s*", "", text).strip(),
                    "_body": [],
                    "candidates": [str(p.relative_to(issue_dir)) for p in matched["images"]],
                    "selected": selected,
                }
                continue
        # 正文段
        if cur_item is not None:
            if re.match(r"^文案[：:]", text) or re.match(r"^.*审核[：:]", text):
                close_item()
                sections_credits = True
                cur_sec.setdefault("credits", []).append(text)
                continue
            cur_item["_body"].append(text)
        else:
            if re.match(r"^文案[：:]", text) or "审核：" in text:
                cur_sec.setdefault("credits", []).append(text)
            else:
                warnings.append(f"无处安放的段落已忽略: {text[:40]}")
    close_section()

    # credits 归并到顶层
    credits: list[str] = []
    for s in sections:
        credits.extend(s.pop("credits", []) or [])
    if not credits:
        warnings.append(
            "docx 无署名段：用 python3 scripts/fetch_staff.py <期号> --apply <期目录> "
            "从飞书「周报工作表」拉取「文案/文案审核」后人工核对"
        )

    # 未进入任何章节的尾部 credits（docx 末尾）
    for para in paras[body_start:]:
        if re.match(r"^文案[：:]", para["text"]):
            if para["text"] not in credits:
                credits.append(para["text"])

    return {
        "schema": "bilibili-weekly-article/1.0",
        "issue": issue_no,
        "period": period,
        "source": {
            "docx": docx.name,
            "image_pack": "图包",
            "cover": cover.name if cover else None,
            "readlist": readlist,
        },
        "title": f"【Gal周报{issue_no}期】{subtitle}" if issue_no else subtitle,
        "title_status": "proposed",
        "subtitle": subtitle,
        "intro": INTRO_TEMPLATE,
        "sections": sections,
        "credits": credits,
        "warnings": warnings,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="解析周报期目录素材 → article.json")
    ap.add_argument("issue_dir", help="期目录，如 '264期'")
    ap.add_argument("--output", help="输出路径（默认 <期目录>/_preview/article.json）")
    ap.add_argument("--readlist", default=None, help="目标文集，如 rl471774")
    args = ap.parse_args()

    issue_dir = Path(args.issue_dir).expanduser().resolve()
    preview = preview_directory(issue_dir)
    if not issue_dir.is_dir():
        raise SystemExit(f"[parse] 目录不存在: {issue_dir}")

    data = parse(issue_dir, args.readlist)

    out = Path(args.output) if args.output else preview / "article.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    n_items = sum(len(s["items"]) for s in data["sections"])
    n_imgs = sum(len(i["candidates"]) for s in data["sections"] for i in s["items"])
    print(f"[parse] docx={data['source']['docx']}")
    print(f"[parse] 期号={data['issue']} 周期={data['period']}")
    print(f"[parse] 章节={len(data['sections'])} 新闻={n_items} 候选图={n_imgs}")
    for s in data["sections"]:
        sel = [len(i["selected"]) for i in s["items"]]
        print(f"  - {s['heading']}: {len(s['items'])} 条, 默认选图 {sel}")
    if data["warnings"]:
        print(f"[parse] 警告 {len(data['warnings'])} 条:")
        for w in data["warnings"]:
            print(f"  ! {w}")
    print(f"[parse] 已写出 {out}")


if __name__ == "__main__":
    main()
