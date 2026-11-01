#!/usr/bin/env python3
"""把 article.json 存为 B 站图文草稿（不发布，供人工在创作中心审核后手动点发布）。

端点与载荷格式均实证自创作中心新编辑器（member.bilibili.com/york/read-editor）：
  POST https://api.bilibili.com/x/dynamic/feed/article/draft/add?csrf=<bili_jct>
  body: {"arg": {"type":4, "template_id":1, "category_id":15, "title":..., "private_pub":2,
                 "reprint":0, "original":1, ..., "opus": {"opus_source":2, "title":...,
                 "content": {"paragraphs":[...]}, "pub_info":..., "attachments":...}}}
  resp: {"code":0, "data": {"article_id": <草稿ID>}}

流程：
  1. 读取 _preview/article.json（人工定稿后）
  2. 上传入选配图到 BFS（同 build_opus.py）
  3. POST draft/add，回执写 _preview/draft_receipt.json
  4. 人工到 创作中心→专栏投稿→草稿箱 打开审核，点「发布」

重复保护：已有回执时默认停止（避免覆盖人工改过的草稿）；不自动删除或覆盖已有草稿。

用法:
    python3 save_draft.py <期目录> [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from safety import preview_directory

from build_opus import load_credential
from safety import selected_images, require_review, require_account, claim_receipt, write_receipt, post_draft

DRAFT_ADD_URL = "https://api.bilibili.com/x/dynamic/feed/article/draft/add"
DRAFT_LIST_URL = "https://api.bilibili.com/x/article/creative/draft/list"
EDITOR_VERSION = "eva3-4.0.0"


# ---------------------------------------------------------------- 编辑器格式段落
# 与 create/opus 的差异：文本节点用 "node_type": 1（非 "type": "TEXT_NODE_TYPE_WORD"），
# 无 align/rich/formula/user 字段；word 带 color/dark_color 空值。空段 nodes 为 []。

# 图片上传与登录会话走共享模块（requests 直传 120s 超时+重试，库版大图必超时）
from upload_bfs import api_session, image_size, upload_images  # noqa: E402

def _word(words: str, font_size: int, bold: bool, gray: bool) -> dict:
    w = {
        "words": words,
        "font_size": font_size,
        "color": "var(--Ga5, #9499a0)" if gray else "",
        "dark_color": "#757A81" if gray else "",
        "style": {"bold": True} if bold else {},
        "font_level": "regular",
    }
    return w


def text_para(words: str, font_size: int = 17, bold: bool = False, gray: bool = False) -> dict:
    if not words:
        return {"para_type": 1, "format": {"indent": {"first_line_indent": 0, "indent": 0}}, "text": {"nodes": []}}
    return {
        "para_type": 1,
        "format": {"indent": {"first_line_indent": 0, "indent": 0}},
        "text": {"nodes": [{"node_type": 1, "word": _word(words, font_size, bold, gray)}]},
    }


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
            raise SystemExit(f"[draft] 封面未上传/未找到: {cover}")
        paras.append(pic_para(imgs[key]))
        paras.append(text_para(""))
    for sec in data.get("sections", []):
        paras.append(text_para(sec.get("heading", sec.get("key", "")), font_size=24, bold=True))
        for idx, item in enumerate(sec.get("items", [])):
            if idx > 0:
                paras.append(text_para(""))
            paras.append(text_para(item.get("heading", ""), bold=True))
            sel = item.get("selected", [])
            if sel:
                key = str((issue_dir / item["candidates"][sel[0]]).resolve())
                if key not in imgs:
                    raise SystemExit(f"[draft] 配图未上传: {item['candidates'][sel[0]]}")
                paras.append(pic_para(imgs[key]))
            for b in item.get("body", []):
                paras.append(text_para(b))
            if len(sel) > 1:
                key = str((issue_dir / item["candidates"][sel[1]]).resolve())
                if key not in imgs:
                    raise SystemExit(f"[draft] 配图未上传: {item['candidates'][sel[1]]}")
                paras.append(pic_para(imgs[key]))
    for c in data.get("credits", []) or []:
        paras.append(text_para(""))
        for line in c.split("\n"):
            if line.strip():
                paras.append(text_para(line))
    return paras


def build_arg(data: dict, paragraphs: list[dict]) -> dict:
    title = data.get("title", "")
    summary = "\n\n" + (data.get("subtitle") or data.get("intro") or title)
    return {
        "type": 4,
        "template_id": 1,
        "category_id": 15,
        "title": title,
        "private_pub": 2,          # 2=所有人可见
        "reprint": 0,              # 原创内容：不标转载
        "original": 1,             # 勾选原创声明（编辑器中可再调整）
        "list_id": 0,              # 文集：留 0，人工在编辑器「选择文集」拾取
        "comment_selected": 0,
        "up_closed_reply": 0,
        "timer_pub_time": 0,
        "only_fans_level": 0,
        "only_fans_dnd": 0,
        "summary": summary,
        "opus": {
            "opus_source": 2,
            "title": title,
            "content": {"paragraphs": paragraphs},
            "pub_info": {"editor_version": EDITOR_VERSION},
            "attachments": {"is_aigc": 0},
        },
    }


def verify_draft(sess: requests.Session, article_id: int) -> dict | None:
    """回读草稿列表确认落地。"""
    r = sess.get(DRAFT_LIST_URL, params={"pn": 1, "ps": 10, "keyword": ""}, timeout=15)
    for d in (r.json().get("data") or {}).get("artlist", {}).get("drafts", []):
        if d.get("id") == article_id:
            return d
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description="保存 B 站图文草稿（人工审核后手动发布）")
    ap.add_argument("issue_dir", help="期目录")
    ap.add_argument("--article", help="article.json（默认 <期目录>/_preview/article.json）")
    ap.add_argument("--credential", help="凭证 env 文件")
    ap.add_argument("--dry-run", action="store_true", help="只构建 arg 不提交")
    ap.add_argument("--account-id", help="目标 B 站 UID（存草稿必填）")
    args = ap.parse_args()

    # 复用共享上传模块
    issue_dir = Path(args.issue_dir).expanduser().resolve()
    preview = preview_directory(issue_dir)
    article_path = Path(args.article) if args.article else preview / "article.json"
    receipt_path = preview / "draft_receipt.json"
    data = json.loads(article_path.read_text(encoding="utf-8"))

    if receipt_path.exists() and not args.dry_run:
        raise SystemExit("[draft] 已有草稿或未知提交回执，请先到草稿箱核对；不自动覆盖或重建")
    wanted = selected_images(issue_dir, data)
    cred, values = (None, {}) if args.dry_run else load_credential(args.credential)
    if not args.dry_run:
        require_review(data)
        if cred is None:
            raise SystemExit("[draft] 未找到登录凭证，请先运行 login_qr.py")

    if args.dry_run:
        imgs = {
            str(p.resolve()): {
                "url": f"__DRY_RUN_IMG_{i}__",
                **dict(zip(("width", "height", "size"), image_size(p))),
            }
            for i, p in enumerate(wanted)
        }
        print(f"[draft] dry-run：{len(imgs)} 张占位图，未登录未上传")
    else:
        sess = api_session(cred)
        require_account(sess, args.account_id)
        imgs = upload_images(sess, cred, wanted)

    paragraphs = build_paragraphs(data, issue_dir, imgs)
    arg = build_arg(data, paragraphs)
    n_pic = sum(1 for p in paragraphs if p.get("para_type") == 2)
    print(f"[draft] 标题: {arg['title']}")
    print(f"[draft] 段落 {len(paragraphs)}（图片段 {n_pic}），原创声明 original=1")

    if args.dry_run:
        out = preview / "draft_arg.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"arg": arg}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[draft] dry-run 完成，arg 已写出 {out}")
        return

    # Claim before any draft write. Keep unknown submissions for manual recovery.
    require_account(sess, args.account_id)
    claim_receipt(receipt_path, {"status": "submitting", "account_id": args.account_id,
                                 "title": arg["title"]})
    resp = post_draft(sess, DRAFT_ADD_URL, params={"csrf": cred.bili_jct}, json={"arg": arg}, timeout=30)
    if resp.get("code") != 0:
        raise SystemExit(f"[draft] 保存未确认 (code={resp.get('code')})，保留回执，请先检查草稿箱")
    article_id = (resp.get("data") or {}).get("article_id")
    if not str(article_id or "").isdigit():
        raise SystemExit("[draft] 未取得草稿 ID，保留未知回执，请先检查草稿箱")
    print(f"[draft] 已保存草稿 article_id={article_id}")

    write_receipt(receipt_path, {"status": "submitted", "account_id": args.account_id,
                                 "article_id": article_id, "title": arg["title"]})

    # 回读验证
    draft = verify_draft(sess, article_id)
    if draft:
        print(f"[draft] 回读确认: state={draft.get('state')} title={draft.get('title', '')[:40]}")
    else:
        print("[draft] 警告: 草稿列表暂未回读到（可能延迟），请到草稿箱确认")

    receipt = {
        "status": "saved",
        "account_id": args.account_id,
        "article_id": article_id,
        "title": arg["title"],
        "paragraphs": len(paragraphs),
        "pics": n_pic,
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "edit_url": "https://member.bilibili.com/platform/upload/text",
        "verified": draft is not None,
    }
    write_receipt(receipt_path, receipt)
    print(f"[draft] 回执已写 {receipt_path}")
    print("[draft] 下一步（人工）: 创作中心→专栏投稿→草稿箱 → 打开该草稿审核 → 点「发布」")


if __name__ == "__main__":
    main()
