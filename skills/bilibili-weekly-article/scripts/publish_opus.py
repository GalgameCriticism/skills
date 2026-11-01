#!/usr/bin/env python3
"""发布 B 站图文（Opus）并回读验证。

前置: build_opus.py 已产出真实 payload（图片已上传，无 __DRY_RUN_IMG 占位）。
发布端点为 x/dynamic/feed/create/opus（wbi 签名），回读用 opus/detail。

用法:
    python3 publish_opus.py <期目录> --yes            # 确认后发布
    python3 publish_opus.py <期目录> --dry-run        # 只校验不发布
    python3 publish_opus.py <期目录> --collection 471774  # 尝试写入文集（实验性）

回执写入 <期目录>/_preview/publish_receipt.json；公开链接为
https://www.bilibili.com/opus/<dyn_id_str>。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from safety import preview_directory

sys.path.insert(0, str(Path(__file__).parent))
from build_opus import load_credential
from upload_bfs import api_session
from safety import (selected_images, require_review, require_account, validate_payload,
                    claim_receipt, write_receipt, digest)

CREATE_OPUS_URL = "https://api.bilibili.com/x/dynamic/feed/create/opus"
OPUS_DETAIL_URL = "https://api.bilibili.com/x/polymer/web-dynamic/v1/opus/detail"


def count_pics(payload: dict) -> int:
    paras = payload["opus_req"]["opus"]["content"]["paragraphs"]
    return sum(1 for p in paras if p.get("para_type") == 2)


async def verify_opus(dyn_id: str, expect_title: str, expect_pics: int) -> dict:
    """回读已发布图文，核对标题与图片数。"""
    from bilibili_api.utils.network import Api

    api = Api(url=OPUS_DETAIL_URL, method="GET", comment="opus 详情回读", verify=False)
    detail = await api.update_params(id=dyn_id, features="itemOpusStyle").result
    item = (detail or {}).get("item", {})
    mods = item.get("modules", [])
    title = ""
    n_pics = 0
    for m in mods:
        if m.get("module_type") == "MODULE_TYPE_TITLE":
            title = m.get("module_title", {}).get("text", "")
        if m.get("module_type") == "MODULE_TYPE_CONTENT":
            for p in m.get("module_content", {}).get("paragraphs", []):
                if p.get("para_type") == 2:
                    n_pics += len(p.get("pic", {}).get("pics", []))
    return {
        "read_back_title": title,
        "read_back_pics": n_pics,
        "title_match": title.replace(" - 哔哩哔哩", "") == expect_title,
        "pics_match": n_pics == expect_pics,
        "collection_id": item.get("basic", {}).get("collection_id"),
    }


async def main_async() -> None:
    ap = argparse.ArgumentParser(description="发布 B 站图文 Opus 并回读验证")
    ap.add_argument("issue_dir", help="期目录")
    ap.add_argument("--article", help="article.json（默认 <期目录>/_preview/article.json）")
    ap.add_argument("--payload", help="opus_payload.json（默认 <期目录>/_preview/opus_payload.json）")
    ap.add_argument("--credential", help="凭证 env 文件")
    ap.add_argument("--collection", default=None, help="文集 ID（实验性，如 471774）")
    ap.add_argument("--no-wbi", action="store_true", help="不附加 wbi 签名（接口变更时的回退）")
    ap.add_argument("--account-id", help="目标 B 站 UID（发布必填）")
    ap.add_argument("--yes", action="store_true", help="跳过交互确认直接发布")
    ap.add_argument("--dry-run", action="store_true", help="只校验 payload 与凭证，不发布")
    args = ap.parse_args()

    issue_dir = Path(args.issue_dir).expanduser().resolve()
    preview = preview_directory(issue_dir)
    article_path = Path(args.article) if args.article else preview / "article.json"
    payload_path = Path(args.payload) if args.payload else preview / "opus_payload.json"
    article = json.loads(article_path.read_text(encoding="utf-8"))
    payload = json.loads(payload_path.read_text(encoding="utf-8"))

    # 1. payload 校验
    if not args.dry_run and "__DRY_RUN_IMG_" in json.dumps(payload):
        raise SystemExit("[publish] payload 含 dry-run 占位图，请先真实运行 build_opus.py")
    title = payload["opus_req"]["opus"]["title"]
    n_pics = count_pics(payload)
    print(f"[publish] 期号={article.get('issue')} 标题={title}")
    print(f"[publish] 图片段={n_pics} upload_id={payload['opus_req']['upload_id']}")

    require_review(article)
    paths = selected_images(issue_dir, article)
    if args.dry_run:
        # Offline validation also works without credentials or bilibili-api installed.
        validate_payload(payload, article, payload.get("_local", {}).get("account_id"), paths)
        print("[publish] dry-run 完成：素材与定稿一致，未登录、未上传、未发布")
        return
    validate_payload(payload, article, args.account_id, paths)
    cred, _ = load_credential(args.credential)
    if cred is None:
        raise SystemExit("[publish] 缺少凭证，请先运行 scripts/login_qr.py")
    require_account(api_session(cred), args.account_id)
    receipt_path = preview / "publish_receipt.json"
    identity = {"issue": article.get("issue"), "account_id": str(args.account_id),
                "payload_sha256": digest(payload), "collection_requested": args.collection}
    if receipt_path.exists():
        previous = json.loads(receipt_path.read_text(encoding="utf-8"))
        if any(previous.get(key) != value for key, value in identity.items()):
            raise SystemExit("[publish] 已有其他素材或账号的回执，请先人工核对，不重复提交")
        dyn_id = str(previous.get("dyn_id") or "")
        if not dyn_id.isdigit():
            raise SystemExit("[publish] 上次提交结果未知，请先检查创作中心，不得盲目重试")
        verify = await verify_opus(dyn_id, title, n_pics)
        write_receipt(receipt_path, dict(previous, verify=verify))
        print(f"[publish] 已有投稿，仅回读： https://www.bilibili.com/opus/{dyn_id}")
        if not (verify["title_match"] and verify["pics_match"]):
            raise SystemExit("[publish] 回读不一致，请人工核对；未重复发布")
        return

    # 3. 人工确认
    if not args.yes:
        ans = input(f"确认发布《{title}》？输入 yes 继续: ").strip()
        if ans != "yes":
            raise SystemExit("[publish] 已取消")

    # 4. 发布
    from bilibili_api.utils.network import Api

    request_payload = {key: value for key, value in payload.items() if key != "_local"}
    opus = request_payload["opus_req"]["opus"]
    if args.collection:
        opus["collection_id"] = int(args.collection)
        print(f"[publish] 实验性参数: collection_id={args.collection}")
    api = Api(
        url=CREATE_OPUS_URL,
        method="POST",
        comment="创建图文专栏 Opus",
        wbi=not args.no_wbi,
        verify=True,
        json_body=True,
        credential=cred,
    )
    upload_id = payload["opus_req"]["upload_id"]
    receipt = dict(identity, status="submitting", title=title, pics=n_pics)
    claim_receipt(receipt_path, receipt)
    print("[publish] 提交 create/opus …", flush=True)
    t0 = time.time()
    try:
        result = await (
            api.update_data(**request_payload)
            .update_params(
                csrf=cred.bili_jct,
                gaia_source="main_web",
                **{"w_opus_req.upload_id": upload_id},
            )
            .result
        )
    except Exception:
        raise SystemExit("[publish] 提交未确认，已保留回执；请先检查创作中心，不直接重试")
    print(f"[publish] 接口已响应 ({time.time()-t0:.1f}s)")

    dyn_id = str(result.get("dyn_id_str") or result.get("dyn_id") or "")
    if not dyn_id.isdigit():
        raise SystemExit("[publish] 响应缺少有效 dyn_id，保留未知回执，请先检查创作中心")
    receipt.update(status="submitted", dyn_id=dyn_id, url=f"https://www.bilibili.com/opus/{dyn_id}")
    write_receipt(receipt_path, receipt)

    # 5. 回读验证
    await asyncio.sleep(3)
    verify = await verify_opus(dyn_id, title, n_pics)
    url = f"https://www.bilibili.com/opus/{dyn_id}"
    receipt = {
        **identity,
        "status": "published" if verify["title_match"] and verify["pics_match"] else "submitted",
        "schema": "bilibili-weekly-article/receipt/1.0",
        "issue": article.get("issue"),
        "title": title,
        "dyn_id": dyn_id,
        "dyn_rid": result.get("dyn_rid"),
        "url": url,
        "pics": n_pics,
        "collection_id": verify.get("collection_id"),
        "collection_requested": args.collection,
        "verify": verify,
        "published_at": time.strftime("%Y-%m-%d %H:%M:%S %z"),
    }
    write_receipt(receipt_path, receipt)

    print()
    if verify["title_match"] and verify["pics_match"]:
        print(f"[publish] 已发布并回读通过：{url}")
    else:
        print(f"[publish] 已提交但回读不一致，请人工核对：{url}")
        print(f"[publish] verify={json.dumps(verify, ensure_ascii=False)}")
    if not verify.get("collection_id") and args.collection:
        print("[publish] 文集参数未生效：请到创作中心→内容管理→专栏管理 手动将该文加入文集")
    elif not verify.get("collection_id"):
        print("[publish] 未指定文集：如需收入「每周Galgame新闻速递」文集，请到创作中心→专栏管理→选择文集")
    print(f"[publish] 回执: {receipt_path}")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
