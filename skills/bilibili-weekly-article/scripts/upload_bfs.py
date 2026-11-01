#!/usr/bin/env python3
"""B 站 BFS 配图上传（requests 直传，120s 超时 + 重试）。

背景（2026-10-12 264期实证）：库版 bilibili_api.dynamic.upload_image 走 aiohttp
且不支持 timeout 参数，本机网络下上传 >1MB 配图稳定超时；requests 直传同一端点
则 42/42 成功。故 build_opus（直接发布）与 save_draft（存草稿）统一走本模块。

端点: POST https://api.bilibili.com/x/dynamic/feed/draw/upload_bfs
      params csrf=<bili_jct>; multipart: file_up, biz=new_dyn, category=daily
返回: data.image_url / data.image_width / data.image_height / data.img_size
      （部分响应用 img_src/url 顶层键，均兼容；字段在 data 内而非顶层）

用法:
    from upload_bfs import api_session, upload_images, image_size
    sess = api_session(cred)
    imgs = upload_images(sess, cred, [Path("封面.png"), ...])
"""
from __future__ import annotations

import json
import time
from pathlib import Path

UPLOAD_URL = "https://api.bilibili.com/x/dynamic/feed/draw/upload_bfs"


def image_size(path: Path) -> tuple[int, int, float]:
    """(宽, 高, KB)。优先 PIL，失败回退 (0, 0, 0)。"""
    try:
        from PIL import Image

        with Image.open(path) as im:
            w, h = im.size
        return w, h, round(path.stat().st_size / 1024, 2)
    except Exception:
        return 0, 0, 0


def api_session(cred) -> "requests.Session":
    """带 B 站登录 cookie 与创作中心 Web 头的 requests 会话。"""
    import requests

    sess = requests.Session()
    sess.cookies.set("SESSDATA", cred.sessdata, domain=".bilibili.com")
    sess.cookies.set("bili_jct", cred.bili_jct, domain=".bilibili.com")
    if getattr(cred, "buvid3", ""):
        sess.cookies.set("buvid3", cred.buvid3, domain=".bilibili.com")
    sess.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
        "Referer": "https://member.bilibili.com/platform/upload/text/new-article",
        "Origin": "https://member.bilibili.com",
    })
    return sess


def upload_one(sess, cred, path: Path, retries: int = 2) -> dict:
    """上传单张图 → {url, width, height, size}；重试后仍失败即中止（防止图片丢失）。"""
    from PIL import Image

    with Image.open(path) as image:
        image.verify()
        mime = Image.MIME[image.format]
    w, h, kb = image_size(path)
    last_err = ""
    for attempt in range(retries + 1):
        try:
            print(f"[upload] {path.name} …", flush=True)
            with open(path, "rb") as f:
                r = sess.post(
                    UPLOAD_URL,
                    params={"csrf": cred.bili_jct},
                    files={"file_up": (path.name, f, mime)},
                    data={"biz": "new_dyn", "category": "daily"},
                    timeout=120, allow_redirects=False,
                )
            r.raise_for_status()
            resp = r.json()
            d = resp.get("data") or {}
            url = d.get("image_url") or d.get("img_src") or d.get("url")
            if resp.get("code") == 0 and url:
                print(f"[upload]   -> {url[:80]}", flush=True)
                return {
                    "url": url,
                    "width": int(d.get("image_width") or w),
                    "height": int(d.get("image_height") or h),
                    "size": float(d.get("img_size") or kb),
                }
            last_err = json.dumps(resp, ensure_ascii=False)[:200]
        except Exception as e:  # noqa: BLE001
            last_err = type(e).__name__  # Request URLs may contain csrf credentials.
        print(f"[upload] 第 {attempt + 1} 次失败: {last_err}", flush=True)
        if attempt == retries:
            break
        time.sleep(2 * (attempt + 1))
    raise SystemExit(f"[upload] 上传失败 {path}: {last_err}")


def upload_images(sess, cred, paths: list[Path], retries: int = 2) -> dict[str, dict]:
    """顺序上传（BFS 对大图并发敏感，顺序最稳），返回 {str(path): img_dict}。"""
    out: dict[str, dict] = {}
    for i, p in enumerate(paths, 1):
        print(f"[upload] ({i}/{len(paths)})", end=" ", flush=True)
        out[str(p)] = upload_one(sess, cred, p, retries=retries)
    return out
