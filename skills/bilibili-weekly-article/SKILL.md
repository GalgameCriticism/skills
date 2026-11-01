---

name: bilibili-weekly-article  
description: 把周报期目录里的 docx 文字稿和图包素材排版成 B 站图文（Opus 专栏）并投稿。用户要求制作/排版/发布 B 站 Gal 周报图文、把文字稿和图包做成专栏文章时使用；由用户人工审核后点击发送。  
---

# B 站周报图文排版与投稿

把期目录中的**原始文字稿 docx + 图包素材**排版为 B 站图文专栏（Opus，`article_type=4`），  
生成本地预览供人工审核，再由用户确认后发布到指定账号，并可选择收入文集。

本技能只负责「素材 → B 站图文」这一段。B 站图文发布后的跨平台搬运  
（小黑盒/知乎/微博）由 [weekly-cross-platform-sync](../weekly-cross-platform-sync/SKILL.md) 编排，  
不要在本技能内实现其他平台。

## 工作流（四步，人工审核卡在第三步）

1. **解析素材**：`python3 scripts/parse_materials.py <期目录> --readlist rl471774`
   ——自动选**标题带「去注音」的 docx**（原始稿含 `【注音】` 括号会漏进正文；
   目录内无去注音版、或正文检出【…】残留时脚本告警，**未清除不得上线**）；
2. **生成预览**：`python3 scripts/render_preview.py <期目录>`，浏览器打开 `_preview/preview.html`
3. **人工审核并定稿**（必做，不可跳过）：
   - 标题定稿：默认拟制「【Gal周报N期】+副标题」，人工改写后把 article.json 的  
     `title_status` 改为 `manual`；
   - 逐条核对配图：默认每条取图包前 2 张（首图在小标题后、次图在正文后），  
     可改 article.json 的 `selected` 索引数组，重跑步骤 2；
   - 补 `credits`：docx 不含署名段，用 `python3 scripts/fetch_staff.py <期号> --apply <期目录>`  
     从飞书「周报工作表」拉取「文案/文案审核」（首次需 lark-cli 扫码授权，详见  
     [references/feishu-staff-table.md](references/feishu-staff-table.md)），拉取后人工核对；
   - 核对章节顺序、正文无外链、无「（简）」前缀残留。
4. **上线**（二选一）：
   - **草稿模式（推荐）**：`python3 scripts/save_draft.py <期目录> --account-id <目标UID>`（上传配图 +
     存草稿），然后人工到 创作中心→专栏投稿→草稿箱 打开草稿审核，点「发布」；
   - 直接发布：`python3 scripts/build_opus.py <期目录> --account-id <目标UID>`（上传配图到 B 站 BFS，
     产出 opus_payload.json）→ `python3 scripts/publish_opus.py <期目录> --account-id <目标UID> --yes`
     （发布 + 回读验证，输出公开链接）。

任何一步的解析警告（`article.json` 的 `warnings`）必须清零或人工确认忽略后才能上线。

## 版式规范

版式以文集「每周Galgame新闻速递」最新一期为实证基准（2026-10 提取自 cv53384810，  
详见 [references/layout-spec.md](references/layout-spec.md)）。要点：

| 区块  | 规格                                   |
| --- | ------------------------------------ |
| 标题  | `【Gal周报N期】<副标题>`，≤ 80 字              |
| 首行  | 灰色小字「本文首发于公众号 Galgame批评」             |
| 封面  | 期目录「*文章封面*.png」，横幅图，作为正文第一张图         |
| 章节  | 新作资讯 / 汉化情报 / 周边信息，24px 加粗，顺序固定      |
| 新闻条 | 小标题 17px 加粗 → 首图 → 正文段 → 次图（可选），条间空段 |
| 结尾  | 文案/审核署名段                             |

docx 段落样式 `2` = 章节标题、`3` = 新闻小标题；纯 URL 段与正文尾部外链在排版时剥离；  
「（简）」前缀从小标题剥离。清洗规则与例外见 [references/material-contract.md](references/material-contract.md)。

**选档硬规则**：文字稿必须用标题带「去注音」的版本（如「去注音264 期 ….docx」），
原始稿的【注音】括号会漏进正文；脚本对回退原始稿/正文残留均会告警，
告警未处理不得上线。

## 素材契约（parse_materials.py 输出）

`_preview/article.json`：

- `issue` / `period` / `subtitle` / `title` / `title_status`（proposed 待定稿 / manual 已定稿）
- `source.docx`、`source.image_pack`、`source.cover`、`source.readlist`
- `intro`（首发声明模板常量）
- `sections[].items[]`：`n`（序号）、`heading`、`body[]`、`candidates[]`（图包内全部候选图，  
  相对期目录）、`selected[]`（入选候选图索引，默认前 2 张）
- `credits[]`（文案/文案审核，来源飞书「周报工作表」，见 fetch_staff.py）、`warnings[]`

只读素材；除 `_preview/` 外不改写期目录任何文件。

## 署名来源（飞书）

docx 不含署名段。文案/文案审核的权威来源是飞书知识库「周报工作表」  
（wiki 节点 `LEfXw6TgTig84ok9XSLcwXsYnnf` → base `WGqUbFqngaCimisbw2Zchy56nwh` →  
表 `tblppBFurScpRucb`，按 `周报期数` 匹配）。查询已固化为  
`python3 scripts/fetch_staff.py <期号> [--apply <期目录>]`，首次使用需 lark-cli  
user 身份扫码授权（三个只读 scope）。264 期实证：表格值与人工填写值一致。  
链路细节、命令与坑见 [references/feishu-staff-table.md](references/feishu-staff-table.md)。

## 发布路径

发布端点为 B 站图文创建接口 `x/dynamic/feed/create/opus`（wbi 签名），  
先经 `x/dynamic/feed/draw/upload_bfs` 上传配图（统一走 `scripts/upload_bfs.py`  
requests 直传：120s 超时+重试，库版 aiohttp 不支持 timeout 大图必超时），  
段落级图文混排与该基准一致。  
接口细节、与 [adoresever/bilibili-mcp](https://github.com/adoresever/bilibili-mcp) 的能力对比  
（其 `bili_send_opus` 只能「正文 + 图片尾随」，无法实现本条图文混排，故作能力参考而非直接依赖）  
见 [references/opus-publish-api.md](references/opus-publish-api.md)。

登录：`python3 scripts/login_qr.py` 扫码登录（自包含实现，QR 同时输出 PNG 与  
终端文字版；轮询 86101 等待扫描 / 86090 已待确认 / 0 成功；成功回调优先从 url query  
取 cookie，为空则访问回调页以 Set-Cookie 兜底，并经 nav 接口验证 isLogin 后才写盘）。  
凭证写 `~/.config/bilibili-weekly-article/config.env`（chmod 600，勿入仓库）。  
也可在 config.env 预置 SESSDATA/BILI_JCT/BUVID3/DEDE_USER_ID。  
需要后台托管长轮询（如 agent 任务环境）时用 `scripts/login_runner.py --qr <png> --log <log>`，
由调用方以后台任务方式运行，凭证同样写 config.env。
（`scripts/login.py` 为早期库封装版，B 站新版扫码响应把 cookie 移到 Set-Cookie 头后  
已失效，勿用；本仓库不收录该文件。）

文集：发布时加 `--collection 471774` 为实验性参数；未生效时按回执提示到  
创作中心→内容管理→专栏管理→选择文集 手动收录。

草稿模式：`python3 scripts/save_draft.py <期目录> --account-id <目标UID>` 走创作中心新编辑器
（member.bilibili.com/york/read-editor，旧版 read/editor 已下线）自用的草稿端点  
`x/dynamic/feed/article/draft/add`（arg.type=4、arg.opus.content.paragraphs 与发布  
同构，文本节点为 `node_type:1`），上传配图后存草稿并回执 `_preview/draft_receipt.json`。  
删除端点 `x/dynamic/feed/article/draft/delete`（body article_id）；`draft/list` 列表  
接口有索引延迟不可靠，回读用 `x/article/creative/draft/view?aid=<id>`。  
已有回执或未知提交时停止，不自动删除、覆盖或重建草稿。先到创作中心核对结果；新版本不提供 `--replace`。
草稿的文集（list_id）留 0，由人工在编辑器「选择文集」拾取。  
264 期实证：42 图全上传、118 段落、article_id=382988，浏览器草稿箱验证版式与线上一致。

## 验收与交付

- 发布成功标准：回读 opus 详情标题一致、图片段数量一致，并给出  
  `https://www.bilibili.com/opus/<dyn_id_str>` 公开链接；回执存 `_preview/publish_receipt.json`。
- 回执必填：期号、标题、dyn_id、图片数、collection_id、回读结果、发布时间。
- 重复发布防护：发布前检查 `_preview/publish_receipt.json`；已有回执且回读通过时  
  直接给出原链接，不重复投稿。
- 发布响应超时或代码非 0 时，先回读（opus/detail + 用户创作中心）再决定重试，  
  不盲目重复提交。
- 最终回复必须带公开链接；仅有草稿/本地文件时如实标注「未发布」。

## 边界

- 不在本技能内搬运其他平台、下载 B 站既有文章或重抓素材。
- 不改写 docx 与图包原始文件；所有派生文件只进 `_preview/`。
- 视频文件（`*视频_*.mp4`）不属于图文素材，忽略；周报视频投稿不在本技能范围。
- 不保存 Cookie/凭证到团队仓库或对话；凭证只走 `~/.config/` 与环境变量。

## 安全与 macOS 试用

使用 Python 3.11+，在独立虚拟环境安装本目录 `requirements.txt`。不依赖 Chrome、COM 或 Windows，macOS 可执行解析、预览、构建和 API 发布。飞书署名另需 lark-cli 的目标表只读授权；也可人工填写真实署名，不要求为试用额外授权飞书。

上传/存草稿/发布必须显式传 `--account-id <目标B站UID>`，脚本用 nav 接口核对真实账号；`--yes` 只省略命令行确认，不代表跳过用户审核。标题须为 `title_status=manual`、署名非空、warnings 清空后才能真实上传。素材只允许引用期目录内的可识别图片，拒绝目录穿越和指向目录外的符号链接。

推荐先用 `build_opus.py <期目录> --dry-run`、`save_draft.py <期目录> --dry-run` 和 `render_preview.py` 做离线预演。它们不登录、不上传、不存线上草稿。`publish_opus.py --dry-run` 同样不访问网络，可检查 dry-run payload；占位图绝不能真实发布。真实 build 会上传图片，即使尚未发布。

真实构建会把定稿、素材及账号信息绑定在 payload 的 `_local` 中；改稿、换图、换账号后必须重建。`_local` 不发送给 B 站。发布前独占创建回执；接口超时保留未知状态，先查创作中心，不盲目重试。取得 dyn_id 后立即保存，回读失败时下一次运行只回读原文章。回执及整个 `_preview` 目录应保留在本机，不提交到 Git。

静态和离线测试不能替代账号的实际草稿验收。首次真实试用优先存草稿，人工核对账号、全文和配图后再在 B 站发布。
