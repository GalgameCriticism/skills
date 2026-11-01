# 署名来源：飞书「周报工作表」

docx 文字稿**不含署名段**，B 站图文结尾的「文案 / 文案审核」署名权威来源是飞书知识库里的
周报工作表。2026-10-12 用 264 期实证：表格值与人工填写值完全一致。

## 表位置

- Wiki 页（用户提供的入口）：<https://wv5bx1i0g5.feishu.cn/wiki/LEfXw6TgTig84ok9XSLcwXsYnnf?table=tblppBFurScpRucb&view=vewr3JiIod>
  - 链接里的 `LEfXw6TgTig84ok9XSLcwXsYnnf` 是 **wiki 节点 token**，不能直接当 base token 用；
  - URL 上的 `table=`/`view=` 参数可直接取用（表 `tblppBFurScpRucb`）。
- 节点标题「周报工作表」，`obj_type=bitable`，解析出的 **base token = `WGqUbFqngaCimisbw2Zchy56nwh`**；
- 表 `tblppBFurScpRucb`（周报工作表），259 条记录（264 期时点），字段：
  - `周报期数`（number）——按它匹配期号；
  - `文案`（text）——对应 credits「文案：…」行；
  - `文案审核`（text）——对应 credits「文案审核：…」行；
  - 其余（新闻采集/素材汇总/配音/时轴/轴校/修音/后期/设计/封面/制作辅助/审核/发布/归档）
    是视频链路分工，图文署名不用。
- 记录按 `周报期数` 唯一（264 期命中 1 条：record_id `reczz28LD3jJYbUs`）。

## 查询命令链（lark-cli）

```bash
# 1. wiki 节点 → base token（首次必须，token 不在 URL 里）
lark-cli wiki spaces get_node --params '{"token":"LEfXw6TgTig84ok9XSLcwXsYnnf","obj_type":"wiki"}' --as user --format json
#    → data.node.obj_token = WGqUbFqngaCimisbw2Zchy56nwh

# 2. 全量拉记录到 ndjson（259 条，一次 2000 上限够）
cd /tmp && lark-cli base +record-list --base-token WGqUbFqngaCimisbw2Zchy56nwh \
  --table-id tblppBFurScpRucb --as user --format ndjson --output bw_staff.ndjson --overwrite

# 3. 客户端按 周报期数 == 期号 过滤，取 文案/文案审核
```

以上链路已固化为 `scripts/fetch_staff.py`（首选，勿手敲）：

```bash
python3 scripts/fetch_staff.py 264                    # 试看：打印 credits 两行
python3 scripts/fetch_staff.py 264 --apply "264期"    # 写回 _preview/article.json 的 credits
```

`--apply` 幂等：值一致时不改文件；写回后需重跑 `render_preview.py` 刷新预览，
若已存过草稿，先人工核对已有草稿；不要自动删除重建。

## 授权（lark-cli user 身份）

bot 身份没有 wiki/base 权限（app `cli_a964b2a4a138dcbd` 未申请 scope），必须用 user 身份。
只读 scope 三个：`wiki:node:read`、`base:app:read`、`bitable:app:readonly`。

```bash
# 1. 发起设备码授权（--no-wait 立即返回）
lark-cli auth login --scope "wiki:node:read,base:app:read,bitable:app:readonly" --no-wait --json
#    → device_code + verification_url（600s 有效）

# 2. 把 verification_url 或二维码给用户（lark-cli auth qrcode <url> [--ascii] [-o png]），
#    等用户回复"授权完成"

# 3. 完成登录（沙箱里前台跑会被 SIGTERM，必须放后台执行！）
lark-cli auth login --device-code <device_code>
```

`lark-cli doctor` 的 `user_identity` 为 warn/missing 时即需重新授权；
refresh_token 过期时 CLI 会打印 clearing 提示，同样重走上面三步。

## 坑（2026-10-12 实证）

- **`--filter-json` 对 number 字段无效**：`[["周报期数","==",264]]` 返回 0 条，
  必须全量拉 ndjson 再客户端过滤；
- **`--page-all` 不被 `base +record-list` 支持**（unknown flag），ndjson 模式默认
  limit 2000，259 条一次够；`--page-size` 会映射到 `--limit`（范围 1-200）；
- **`--output` 只允许 cwd、/tmp、~/files**：Python `tempfile` 的 `/var/folders/...`
  会被拒（unsafe output path）；ndjson 模式 stdout 是 manifest（无 `ok` 字段），
  记录在 `manifest.record_file`；
- **field-list 的字段键是 `id`/`name`**（不是 `field_id`/`field_name`）；
- **device-code 完成登录在沙箱里被 SIGTERM**（exit 137、无输出），后台跑 6s 即完成；
- 设备码 600s 过期，过期后 `--device-code` 报 "The device_code is invalid"，
  需重新生成链接让用户再扫一次。
