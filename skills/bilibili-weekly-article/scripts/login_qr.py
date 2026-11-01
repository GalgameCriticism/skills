#!/usr/bin/env python3
"""B 站扫码登录（自包含实现，兼容新版 Set-Cookie 响应头）。

流程:
    1. GET passport web/qrcode/generate  → url + qrcode_key
    2. 二维码存 PNG，人工扫码
    3. 轮询 web/qrcode/poll：86101 已扫描 / 86090 已待确认 / 0 成功
    4. 成功回调：优先从 url query 取 cookie，为空则 GET 该 url 用 Set-Cookie 兜底
    5. nav 接口验证登录态，凭证写入 config.env（chmod 600）

用法:
    python3 login_qr.py [--credential PATH] [--qr PATH] [--log PATH]
"""
from __future__ import annotations

import argparse
import sys
import time
from urllib.parse import urlsplit
from pathlib import Path
from safety import write_private

import requests

DEFAULT_ENV = Path.home() / ".config" / "bilibili-weekly-article" / "config.env"

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
GENERATE = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
POLL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"
NAV = "https://api.bilibili.com/x/web-interface/nav"
WANT = ["SESSDATA", "bili_jct", "DedeUserID", "buvid3", "buvid4", "ac_time_value"]


def log(msg: str, logfile: str | None) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    if logfile:
        with open(logfile, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def cookies_from_url(url: str) -> dict[str, str]:
    """从回调 URL 的 query string 抠 cookie（旧版 B 站行为）。"""
    out: dict[str, str] = {}
    if "?" not in url:
        return out
    for kv in url.split("?", 1)[1].split("&"):
        if "=" in kv:
            k, v = kv.split("=", 1)
            if k in WANT and v:
                out[k] = v
    return out


def verify(sess: requests.Session) -> dict:
    r = sess.get(NAV, headers={"User-Agent": UA, "Referer": "https://www.bilibili.com"}, timeout=15)
    d = r.json()
    data = d.get("data") or {}
    return {
        "code": d.get("code"),
        "isLogin": data.get("isLogin"),
        "uid": data.get("mid"),
        "uname": data.get("uname"),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="B 站扫码登录（自包含）")
    ap.add_argument("--credential", default=str(DEFAULT_ENV))
    ap.add_argument("--qr", default="/tmp/bili_login_qr.png")
    ap.add_argument("--log", default="/tmp/bili_login.log")
    args = ap.parse_args()

    sess = requests.Session()
    sess.headers.update({"User-Agent": UA, "Referer": "https://passport.bilibili.com/"})

    # 1. 生成二维码
    r = sess.get(GENERATE, timeout=15)
    d = r.json()
    if d.get("code") != 0:
        log(f"FAIL generate: {d}", args.log)
        sys.exit(1)
    qr_url, qr_key = d["data"]["url"], d["data"]["qrcode_key"]

    import qrcode

    qrcode.make(qr_url).save(args.qr)
    log(f"二维码已生成: {args.qr}（180s 有效，请用 B 站 App 扫描）", args.log)
    print(f"QR_SAVED {args.qr}", flush=True)

    # 终端版二维码写入日志，供聊天窗口直接展示（双通道）
    try:
        import io

        import qrcode as _qr

        q = _qr.QRCode(border=2)
        q.add_data(qr_url)
        q.make(fit=True)
        buf = io.StringIO()
        q.print_ascii(out=buf)
        with open(args.log, "a", encoding="utf-8") as f:
            f.write("[QR_TERMINAL_BEGIN]\n")
            f.write(buf.getvalue())
            f.write("[QR_TERMINAL_END]\n")
    except Exception as e:
        log(f"终端二维码渲染失败（不影响 PNG）: {e}", args.log)

    # 2. 轮询
    t0 = time.time()
    last = None
    while True:
        r = sess.get(POLL, params={"qrcode_key": qr_key, "source": "main-fe-header"}, timeout=15)
        d = r.json()
        code = d.get("data", {}).get("code")
        if code != last:
            last = code
            msg = {
                86101: "等待扫描…",
                86090: "已扫描，等待手机确认…",
                86038: "二维码过期",
            }.get(code, f"状态码 {code}")
            log(msg, args.log)
        if code == 0:
            break
        if code == 86038 or time.time() - t0 > 180:
            log("FAIL 超时，请重新运行", args.log)
            sys.exit(2)
        time.sleep(2)

    # 3. 取 cookie：url query 优先，Set-Cookie 兜底
    data = d.get("data", {})
    cookies = cookies_from_url(data.get("url", ""))
    src = "url-query"
    if not cookies.get("SESSDATA") or not cookies.get("bili_jct"):
        # 新版：回调 url 只是落地页，cookie 在访问它的 Set-Cookie 响应头里
        cb = data.get("url", "")
        if cb:
            parsed = urlsplit(cb)
            if (parsed.scheme != "https" or not (parsed.hostname or "").endswith(".bilibili.com")
                    or parsed.username or parsed.password or parsed.port not in (None, 443)):
                raise SystemExit("[login] 回调地址不属于 B 站 HTTPS 域名")
            try:
                sess.get(cb, timeout=15, allow_redirects=False)
            except requests.RequestException:
                log("回调页访问异常（继续用已有 cookie）", args.log)
        cookies = {k: v for k, v in sess.cookies.get_dict().items() if k in WANT}
        src = "set-cookie"
    log(f"cookie 来源: {src}，字段: {sorted(k for k in cookies if cookies[k])}", args.log)

    missing = [k for k in ("SESSDATA", "bili_jct", "DedeUserID") if not cookies.get(k)]
    if missing:
        log(f"FAIL 关键 cookie 缺失: {missing}", args.log)
        sys.exit(3)

    # 4. nav 验证
    info = verify(sess)
    log(f"nav 验证: {info}", args.log)
    if not info.get("isLogin"):
        log("FAIL nav 显示未登录，拒绝写入凭证", args.log)
        sys.exit(4)

    # 5. 写凭证（uid 以 nav 为准）
    out = Path(args.credential)
    out.parent.mkdir(parents=True, exist_ok=True)
    uid = str(info.get("uid") or cookies.get("DedeUserID", ""))
    write_private(out,
        "# bilibili-weekly-article 凭证（chmod 600，勿入仓库）\n"
        f"SESSDATA={cookies['SESSDATA']}\n"
        f"BILI_JCT={cookies['bili_jct']}\n"
        f"BUVID3={cookies.get('buvid3', '')}\n"
        f"DEDE_USER_ID={uid}\n",
    )
    log(f"DONE 凭证已写入 {out}（uid={uid}, uname={info.get('uname')}）", args.log)


if __name__ == "__main__":
    main()
