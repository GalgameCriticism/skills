#!/usr/bin/env python3
"""扫码登录 runner：QR 存为 PNG（供人工扫码），后台轮询状态，成功后写凭证。

用法:
    python3 login_runner.py --qr /tmp/bili_login_qr.png --log /tmp/bili_login.log \
        [--credential ~/.config/bilibili-weekly-article/config.env]

轮询进程与调用方分离：本脚本常驻前台由后台任务托管，日志写入 --log。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from safety import write_private

DEFAULT_ENV = Path.home() / ".config" / "bilibili-weekly-article" / "config.env"


async def main_async() -> None:
    ap = argparse.ArgumentParser(description="B 站扫码登录 runner")
    ap.add_argument("--credential", default=str(DEFAULT_ENV), help="凭证输出文件")
    ap.add_argument("--qr", default="/tmp/bili_login_qr.png", help="二维码 PNG 输出路径")
    ap.add_argument("--log", default="/tmp/bili_login.log", help="状态日志路径")
    args = ap.parse_args()

    from bilibili_api import login_v2

    def log(msg: str) -> None:
        with open(args.log, "a", encoding="utf-8") as f:
            f.write(msg + "\n")

    qr = login_v2.QrCodeLogin()
    await qr.generate_qrcode()

    # 二维码渲染为 PNG：qrcode 库为 bilibili-api 依赖，可用
    import qrcode

    url = getattr(qr, "_QrCodeLogin__qr_link", None)
    if not url:
        # 兜底：扫描常见属性名
        for attr in ("qr_link", "url", "qr_url", "_QrCodeLogin__url"):
            url = getattr(qr, attr, None)
            if url:
                break
    if not url:
        print("FAIL no qr url", flush=True)
        sys.exit(4)
    img = qrcode.make(url)
    img.save(args.qr)
    print(f"QR_SAVED {args.qr}", flush=True)
    log(f"[login] 二维码已生成: {args.qr}  (url={url})")

    # 同时存一份终端版到日志，双通道可用
    try:
        log("[login] 终端二维码:\n" + qr.get_qrcode_terminal())
    except Exception:
        pass

    deadline = 180  # 180s 有效期
    import time

    t0 = time.time()
    while True:
        state = await qr.check_state()
        if state == login_v2.QrCodeLoginEvents.SCAN:
            log("[login] 已扫描，等待手机确认…")
        elif state == login_v2.QrCodeLoginEvents.CONF:
            log("[login] 已确认，正在获取凭证…")
        elif state == login_v2.QrCodeLoginEvents.DONE:
            break
        elif state == login_v2.QrCodeLoginEvents.TIMEOUT:
            log("[login] FAIL 二维码超时，请重新生成")
            sys.exit(2)
        if time.time() - t0 > deadline:
            log("[login] FAIL 超过 180s 未完成")
            sys.exit(3)
        await asyncio.sleep(2)

    cred = qr.get_credential()
    out = Path(args.credential)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_private(out,
        "# bilibili-weekly-article 凭证（chmod 600，勿入仓库）\n"
        f"SESSDATA={cred.sessdata}\n"
        f"BILI_JCT={cred.bili_jct}\n"
        f"BUVID3={cred.buvid3}\n"
        f"DEDE_USER_ID={cred.dedeuserid}\n",
    )
    log(f"[login] DONE 凭证已写入 {out} (uid={cred.dedeuserid})")
    print(f"LOGIN_DONE uid={cred.dedeuserid}", flush=True)


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
