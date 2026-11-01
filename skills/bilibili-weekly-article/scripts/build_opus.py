#!/usr/bin/env python3
"""把 article.json 构建成 B 站图文（Opus）发布 payload。

两步:
  1. 上传入选配图到 B 站 BFS（x/dynamic/feed/draw/upload_bfs）
  2. 生成 create/opus 请求体（raw_content + opus_req.opus.content.paragraphs）

用法:
    # 离线演练：不登录，用占位 URL 校验结构
    python3 build_opus.py <期目录> --dry-run

    # 正式构建：先扫码/凭证登录，再上传图片，产出 opus_payload.json
    python3 build_opus.py <期目录>

凭证读取顺序: --credential 指定文件 > 环境变量 > ~/.config/bilibili-weekly-article/config.env
（SESSDATA / bili_jct / buvid3 / DedeUserID）。缺失时提示先运行 login_qr.py。
"""
from __future__ import annotations

import argparse
import json
import os
import random
import time
from pathlib import Path
from safety import preview_directory
from safety import selected_images, require_review, require_account, payload_metadata

CRED_ENV = Path.home() / ".config" / "bilibili-weekly-article" / "config.env"


def load_credential(path: str | None):
    """返回 Credential 或 None（dry-run 允许为 None）。"""
    env_file = Path(path) if path else CRED_ENV
    values = {}
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            values[k.strip()] = v.strip().strip('"').strip("'")
    for k in ("SESSDATA", "BILI_JCT", "BUVID3", "DEDE_USER_ID"):
        if not path and os.environ.get(k):
            values[k] = os.environ[k]
    if not values.get("SESSDATA") or not values.get("BILI_JCT"):
        return None, values
    from bilibili_api import Credential

    cred = Credential(
        sessdata=values["SESSDATA"],
        bili_jct=values["BILI_JCT"],
        buvid3=values.get("BUVID3", ""),
        dedeuserid=values.get("DEDE_USER_ID", ""),
    )
    return cred, values


# 图片上传统一走共享模块（requests 直传，120s 超时+重试；库版 aiohttp 不支持 timeout，
# 大图稳定超时，见 upload_bfs.py 头部说明）
from upload_bfs import api_session, image_size, upload_images  # noqa: E402


def text_para(words: str, font_size: int = 17, bold: bool = False, gray: bool = False) -> dict:
    word = {
        "words": words,
        "font_size": font_size,
        "style": {
            "bold": bold,
            "italic": False,
            "strikethrough": False,
            "underline": False,
            "background": "",
        },
        "font_level": "regular",
    }
    if gray:
        word["color"] = "var(--Ga5, #9499a0)"
        word["dark_color"] = "#757A81"
    return {
        "para_type": 1,
        "align": 2,
        "format": {"align": 2, "indent": {"first_line_indent": 0, "indent": 0}},
        "text": {"nodes": [{"type": "TEXT_NODE_TYPE_WORD", "word": word, "rich": None, "formula": None, "user": None}]},
    }


def empty_para() -> dict:
    return text_para("")


def pic_para(img: dict) -> dict:
    return {
        "para_type": 2,
        "align": 2,
        "format": {"align": 2},
        "text": None,
        "pic": {
            "style": 1,
            "pics": [
                {
                    "url": img["url"],
                    "width": img["width"],
                    "height": img["height"],
                    "size": img["size"],
                    "status": "loaded",
                }
            ],
        },
    }


def build_paragraphs(data: dict, issue_dir: Path, imgs: dict[str, dict]) -> list[dict]:
    paras: list[dict] = []
    if data.get("intro"):
        paras.append(text_para(data["intro"], gray=True))
    cover = data.get("source", {}).get("cover")
    if cover:
        key = str((issue_dir / cover).resolve())
        if key not in imgs:
            raise SystemExit(f"[build] 封面未上传/未找到: {cover}")
        paras.append(pic_para(imgs[key]))
        paras.append(empty_para())
    for sec in data.get("sections", []):
        paras.append(text_para(sec.get("heading", sec.get("key", "")), font_size=24, bold=True))
        for idx, item in enumerate(sec.get("items", [])):
            if idx > 0:
                paras.append(empty_para())
            paras.append(text_para(item.get("heading", ""), bold=True))
            sel = item.get("selected", [])
            if sel:
                key = str((issue_dir / item["candidates"][sel[0]]).resolve())
                if key not in imgs:
                    raise SystemExit(f"[build] 配图未上传: {item['candidates'][sel[0]]}")
                paras.append(pic_para(imgs[key]))
            for b in item.get("body", []):
                paras.append(text_para(b))
            if len(sel) > 1:
                key = str((issue_dir / item["candidates"][sel[1]]).resolve())
                if key not in imgs:
                    raise SystemExit(f"[build] 配图未上传: {item['candidates'][sel[1]]}")
                paras.append(pic_para(imgs[key]))
    for c in data.get("credits", []) or []:
        paras.append(empty_para())
        for line in c.split("\n"):
            if line.strip():
                paras.append(text_para(line))
    return paras


def build_raw_content(data: dict, issue_dir: Path, imgs: dict[str, dict]) -> str:
    """quill delta 版正文（create/opus 与 paragraphs 一并提交）。"""
    ops: list[dict] = []

    def add_text(t: str, bold: bool = False):
        if not t:
            return
        op = {"insert": t + "\n"}
        if bold:
            op["attributes"] = {"bold": True}
        ops.append(op)

    def add_img(img: dict):
        ops.append({
            "attributes": {"class": "normal-img"},
            "insert": {
                "native-image": {
                    "alt": "read-normal-img",
                    "url": img["url"],
                    "width": img["width"],
                    "height": img["height"],
                    "size": int(img["size"] * 1024),
                    "status": "loaded",
                }
            },
        })
        ops.append({"insert": "\n"})

    if data.get("intro"):
        add_text(data["intro"])
    cover = data.get("source", {}).get("cover")
    if cover:
        add_img(imgs[str((issue_dir / cover).resolve())])
    for sec in data.get("sections", []):
        add_text(sec.get("heading", sec.get("key", "")), bold=True)
        for item in sec.get("items", []):
            add_text(item.get("heading", ""), bold=True)
            sel = item.get("selected", [])
            if sel:
                add_img(imgs[str((issue_dir / item["candidates"][sel[0]]).resolve())])
            for b in item.get("body", []):
                add_text(b)
            if len(sel) > 1:
                add_img(imgs[str((issue_dir / item["candidates"][sel[1]]).resolve())])
    for c in data.get("credits", []) or []:
        add_text(c)
    return json.dumps({"ops": ops}, ensure_ascii=False)


async def main_async() -> None:
    ap = argparse.ArgumentParser(description="构建 B 站图文 Opus 发布 payload")
    ap.add_argument("issue_dir", help="期目录")
    ap.add_argument("--article", help="article.json（默认 <期目录>/_preview/article.json）")
    ap.add_argument("--credential", help="凭证 env 文件（默认 ~/.config/bilibili-weekly-article/config.env）")
    ap.add_argument("--account-id", help="目标 B 站 UID（上传必填）")
    ap.add_argument("--dry-run", action="store_true", help="不登录不上传，用占位图校验结构")
    ap.add_argument("--out", help="payload 输出（默认 <期目录>/_preview/opus_payload.json）")
    args = ap.parse_args()

    issue_dir = Path(args.issue_dir).expanduser().resolve()
    preview = preview_directory(issue_dir)
    article_path = Path(args.article) if args.article else preview / "article.json"
    out = Path(args.out) if args.out else preview / "opus_payload.json"
    data = json.loads(article_path.read_text(encoding="utf-8"))

    wanted = selected_images(issue_dir, data)
    if not args.dry_run:
        require_review(data)

    cred, values = (None, {})
    if not args.dry_run:
        cred, values = load_credential(args.credential)
        if cred is None:
            raise SystemExit(
                "[build] 未找到登录凭证。先运行:\n"
                "  python3 scripts/login_qr.py\n"
                "或准备 ~/.config/bilibili-weekly-article/config.env（SESSDATA/BILI_JCT/BUVID3/DEDE_USER_ID）"
            )

    if args.dry_run:
        imgs = {str(p.resolve()): {"url": f"__DRY_RUN_IMG_{i}__", **dict(zip(("width", "height", "size"), image_size(p)))} for i, p in enumerate(wanted)}
        mid = "0"
        print(f"[build] dry-run：{len(imgs)} 张占位图，未登录未上传")
    else:
        mid = values.get("DEDE_USER_ID") or "0"
        session = api_session(cred)
        require_account(session, args.account_id)
        imgs = upload_images(session, cred, wanted)

    paragraphs = build_paragraphs(data, issue_dir, imgs)
    raw_content = build_raw_content(data, issue_dir, imgs)
    upload_id = f"{mid}_{int(time.time())}_{random.randint(1000, 9999)}"
    payload = {
        "raw_content": raw_content,
        "opus_req": {
            "upload_id": upload_id,
            "opus": {
                "opus_source": 2,
                "title": data.get("title", ""),
                "content": {"paragraphs": paragraphs},
            },
        },
    }
    payload["_local"] = payload_metadata(data, args.account_id or "dry-run", wanted)
    from safety import digest
    payload["_local"]["payload_sha256"] = digest({k: v for k, v in payload.items() if k != "_local"})
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    n_pic = sum(1 for p in paragraphs if p.get("para_type") == 2)
    print(f"[build] 段落 {len(paragraphs)}（图片段 {n_pic}），upload_id={upload_id}")
    print(f"[build] payload 已写出 {out}")
    if args.dry_run:
        print("[build] dry-run 完成：结构校验通过，可进入 render_preview 人工审核")


def main() -> None:
    import asyncio

    asyncio.run(main_async())


if __name__ == "__main__":
    main()
