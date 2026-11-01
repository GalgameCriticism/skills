#!/usr/bin/env python3
"""从飞书「周报工作表」拉取指定期数的 staff 署名（文案/文案审核）。

链路（2026-10-12 实证，264期验证与人工填写值一致）：
  wiki 节点 LEfXw6TgTig84ok9XSLcwXsYnnf（标题「周报工作表」）
    → lark-cli wiki spaces get_node 得 bitable obj_token WGqUbFqngaCimisbw2Zchy56nwh
    → 表 tblppBFurScpRucb（周报工作表，259 条记录，字段「周报期数」为 number）
    → record-list ndjson 全量拉取 → 按 周报期数 == issue 客户端过滤
    → 取「文案」「文案审核」两列生成 credits（与 B 站图文结尾署名同格式）。

授权（lark-cli user 身份，token 存 macOS keychain）：
  首次/过期时需扫码，两个只读 scope：
    lark-cli auth login --scope "wiki:node:read,base:app:read,bitable:app:readonly" --no-wait --json
    # 把 verification_url（或 lark-cli auth qrcode 生成的二维码）给用户，等授权完成后：
    lark-cli auth login --device-code <device_code>
  注意：--device-code 完成登录在沙箱里会被 SIGTERM，须放后台跑（run_in_background）。
  record-list 的 --filter-json 对 number 字段不过滤（返回 0 条），必须全量拉 ndjson
  再客户端过滤；--page-all 不被 record-list 支持，ndjson 模式默认 limit 2000 一次够。

用法:
    python3 fetch_staff.py 264                  # 打印 credits 两行（试看）
    python3 fetch_staff.py 264 --apply "264期"   # 写回该期 _preview/article.json 的 credits
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

WIKI_NODE_TOKEN = "LEfXw6TgTig84ok9XSLcwXsYnnf"
TABLE_ID = "tblppBFurScpRucb"


def run_cli(args: list[str]) -> dict:
    """调 lark-cli（user 身份），返回解析后的 JSON；失败即中止。

    注意：不要在此统一加 --format json——record-list 的 --output 写 ndjson
    时会与 --format json 冲突，由调用方按需显式传格式。
    """
    cmd = ["lark-cli", *args, "--as", "user"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    try:
        out = json.loads(r.stdout)
    except json.JSONDecodeError:
        raise SystemExit(f"[staff] lark-cli 输出解析失败: {r.stdout[:200]} {r.stderr[:200]}")
    if not out.get("ok"):
        err = out.get("error", {})
        hint = err.get("hint", "")
        raise SystemExit(
            f"[staff] lark-cli 失败: {err.get('message', out)}\n{hint}\n"
            " 授权过期时重跑:\n"
            '  lark-cli auth login --scope "wiki:node:read,base:app:read,bitable:app:readonly" --no-wait --json'
        )
    return out


def resolve_base_token() -> str:
    node = run_cli([
        "wiki", "spaces", "get_node",
        "--params", json.dumps({"token": WIKI_NODE_TOKEN, "obj_type": "wiki"}),
        "--format", "json",
    ])
    n = node["data"]["node"]
    if n.get("obj_type") != "bitable":
        raise SystemExit(f"[staff] wiki 节点不是多维表格: {n.get('obj_type')}")
    return n["obj_token"]


def fetch_staff(base_token: str, issue: int) -> dict:
    """全量拉记录（ndjson 到 /tmp）后按 周报期数 过滤。

    ndjson 模式 stdout 是 manifest（无 ok 字段，记录写入 manifest.record_file），
    与 json 模式的响应结构不同，故单独走 subprocess。
    lark-cli --output 仅允许 cwd、/tmp、~/files，使用 /tmp 内本次独立目录。
    """
    temporary = tempfile.TemporaryDirectory(prefix="bw-staff-", dir="/tmp")
    tmp = str(Path(temporary.name) / "staff.ndjson")
    cmd = [
        "lark-cli", "base", "+record-list",
        "--base-token", base_token,
        "--table-id", TABLE_ID,
        "--as", "user",
        "--format", "ndjson",
        "--output", tmp,
        "--overwrite",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    try:
        manifest = json.loads(r.stdout)
    except json.JSONDecodeError:
        raise SystemExit(f"[staff] record-list 失败: {r.stdout[:200]} {r.stderr[:200]}")
    record_file = str(Path(manifest.get("record_file", tmp)).resolve())
    if Path(record_file) != Path(tmp).resolve():
        temporary.cleanup()
        raise SystemExit("[staff] record_file 不属于本次临时目录")
    try:
        recs = [json.loads(l) for l in open(record_file, encoding="utf-8") if l.strip()]
    finally:
        temporary.cleanup()
    hit = [r for r in recs if r.get("周报期数") == issue]
    if not hit:
        got = sorted({r.get("周报期数") for r in recs if isinstance(r.get("周报期数"), int)})
        near = [i for i in got if abs(i - issue) <= 3]
        raise SystemExit(
            f"[staff] 表中无第 {issue} 期记录（共 {len(recs)} 条，期数范围 {got[0]}~{got[-1]}）\n"
            f" 相邻期数参考: {near}\n"
            " 可能该期尚未登记，或期目录名与表中间数不一致——请人工核对。"
        )
    return hit[0]


def to_credits(rec: dict) -> list[str]:
    """文案/文案审核 → credits 字符串列表（与 docx 缺署名时的人工填写格式一致）。"""
    credits: list[str] = []
    lines: list[str] = []
    for label, key in (("文案", "文案"), ("文案审核", "文案审核")):
        v = (rec.get(key) or "").strip()
        if v:
            lines.append(f"{label}：{v}")
    if lines:
        credits.append("\n".join(lines))
    return credits


def apply_to_article(issue_dir: Path, credits: list[str]) -> None:
    article_path = issue_dir / "_preview" / "article.json"
    data = json.loads(article_path.read_text(encoding="utf-8"))
    if data.get("credits") == credits:
        print(f"[staff] article.json credits 已一致，未改动: {article_path}")
        return
    data["credits"] = credits
    article_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[staff] 已写回 {article_path}")
    print("[staff] 提醒: 重跑 render_preview.py 刷新预览；已有草稿时先人工核对，不自动删除重建")


def main() -> None:
    ap = argparse.ArgumentParser(description="从飞书周报工作表拉取 staff 署名")
    ap.add_argument("issue", type=int, help="周报期数，如 264")
    ap.add_argument("--apply", metavar="ISSUE_DIR", help="写回指定期目录的 article.json credits")
    args = ap.parse_args()

    base_token = resolve_base_token()
    rec = fetch_staff(base_token, args.issue)
    credits = to_credits(rec)

    print(f"[staff] 第 {args.issue} 期（record_id={rec.get('record_id')}）:")
    for c in credits:
        for line in c.split("\n"):
            print(f"  {line}")
    if not credits:
        print("  （该期文案/文案审核均为空，请人工确认）")

    if args.apply:
        apply_to_article(Path(args.apply).expanduser().resolve(), credits)
    else:
        print("[staff] 试看模式。写回 article.json: 加 --apply <期目录>")


if __name__ == "__main__":
    sys.exit(main())
