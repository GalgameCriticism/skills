---
name: zhihu-weekly-sync
description: 在用户要求把获授权的周报发布到知乎文章/专栏、或需要判断知乎来源读取能力时使用，尤其适用于持久化登录态直排、正文图片保真校验和公开页回读。
---

# 知乎周报同步

本 skill 是知乎发布端适配器：把经核验的周报素材发布为知乎文章。素材可以来自 B 站、微博或用户提供的知乎文章 URL；发布授权来自当前任务，目标账号必须在发布前核对。

## 浏览器与登录态安全边界

遵守 [共享浏览器会话规则](../weekly-cross-platform-sync/references/browser-session.md)：优先连接已登录的独立 profile/CDP。用户明确允许自动启动时，在原进程确已退出且 profile 未被占用的情况下，可用同一可执行文件、原 profile 和原 Cookie/密钥存储配置重新启动；已有授权不重复询问。

不得重置、清理、移动或覆盖 profile，不关闭/杀掉用户浏览器，不使用普通 Chrome `Default`、`Profile *` 或日常用户目录。正常运行的专用 profile 锁不妨碍连接其 CDP；不得删锁或另开进程抢占。登录、账号切换与验证码仍由用户处理，结束后保留用户会话。

## 一个窗口贯穿全流程（禁止反复重启浏览器）

`scripts/browser-session.cjs` 提供 `attach(profileDir, port)`：先尝试 `connectOverCDP` 连接已在运行的专用 Chrome，只有在授权启动、设置 `CHROME_ALLOW_LAUNCH=1` 且探测不到调试端口时才以 `--enable-automation --remote-debugging-address=127.0.0.1 --remote-debugging-port=<port>` 启动一次（detached），随后所有步骤复用同一窗口。

- 知乎默认端口 `9444`（`ZHIHU_DEBUG_PORT` 可覆盖），微博默认 `9333`；两个平台各用一个 profile + 端口，互不干扰。
- 每一步结束都**不关闭**浏览器：下一步重新 `attach` 即可，登录态与已填草稿都留在窗口里。
- 不要再用 `launchPersistentContext` 每步开关浏览器：那会反复弹窗、丢草稿、并把登录态置于风险中。脚本崩溃或超时后也先 `attach` 复查，而不是重开。
- 只有在用户明确要求收尾时才关闭窗口。

## 已验证发布链路（第265期跑通，2026-10）

1. 连接专用 profile（`ZHIHU_PROFILE`）→ `/api/v4/me` 核对昵称必须等于目标账号（`Galgame批评`），不匹配立即停止。
2. **直排正文**：`scripts/zhihu-compose.cjs --manifest <manifest.json> --optimized <图片目录> --cover <封面>`。脚本填标题、逐块粘贴正文、按 manifest 顺序逐张上传图片，并做保真校验。
3. 校验通过后发布：同一脚本加 `--publish`，或对已存在的草稿用 `scripts/zhihu-draft-publish.cjs --manifest <manifest.json> --publish`（打开草稿箱里完全匹配的那篇再发布，避免产生第二篇）。
4. 回读：`scripts/zhihu-verify.cjs`（`ARTICLE=<公开URL> MANIFEST=<manifest>`）核对标题、作者、全文和图片顺序。
5. 最终回复直接给出 `https://zhuanlan.zhihu.com/p/<id>` 公开链接。

### 直排的图片判定（踩过的坑）

- 判定图片“上传完成”必须用托管 URL 的 `/v2-[0-9a-f]{32}` 特征，**不能**用 `zhimg.com` 域名：编辑器在占位/预览阶段也会出现 zhimg 前缀，但 `src` 仍是 `blob:` 或空串。
- 单张图片给足等待（实测最长数分钟），**不要**用“重置 input value 再传一次”的重试：重复触发会上传两张，正文图片数翻倍。脚本里 `attempt <= 1` 是刻意保留的，改成重试前先确认不会重复插入。
- 编辑器容器是 `.public-DraftEditor-content[contenteditable="true"]`；正文块取 `el.children[0].children`，图片计数取容器内 `img`，不要把整页 `innerText` 当正文。
- 全文字符比对需剔除编辑器自动插入的“添加图片注释，不超过140字（可选）”提示。

### DOCX 导入为什么不用

`导入` → `导入文档` → 选择 `.docx` 这条链路在当前账号会被**直答知识库配额**拦截：文件能选中，但“确认选择文件”永不进入可点击态（多次重试、真实鼠标点击、单选按钮、label 点击均无效），不要在这条路上反复消耗时间。仅在直排不可用、且确认配额恢复时才回退 DOCX 导入，并保留完整嵌图与顺序校验。

## 正文图片保真检查

硬性门槛，不满足时不能点击“发布”：

1. 图片数量必须等于 manifest 的图片块数，且每张都有真实 HTTPS 托管地址；`blob:`、空 `src`、占位图都不算成功。
2. 正文中不能残留 `WBIMGPH_`、`![配图](...)`、`images/001.png` 之类的占位或本地路径。
3. 归一化全文必须与 manifest 逐字一致（排除图片注释 UI 文案）。
4. 封面单独检查：`.UploadPicture-input` 上传后必须显示 zhimg 托管地址；封面不计入正文图片数。封面上传失败但正文通过时，可在回执注明“封面未设置”后继续。

## 已发布文章的纠错

如果文章已发布但正文是纯文本/Markdown、或图片缺失：打开同一文章的 `/edit` 页面修正，不要新建第二篇。只有原文章不可编辑时，才说明会产生重复文章并取得用户确认。

## 备用适配器与切换规则

[guoxiaoyi/liduma-article-publisher](https://github.com/guoxiaoyi/liduma-article-publisher)、[yangming0506/zhihu-auto-publish](https://github.com/yangming0506/zhihu-auto-publish) 均曾作为备选验证：若访问写作页被重定向到登录页，即使本地能看到 `z_c0` 也必须停止。这些项目可能在失败后打印“流程已完成”，只有公开文章 URL 与公开页回读才算成功；不要在同一 profile 上盲目重复三次。

## 兼容方案：安装知乎 MCP

上游项目 [Victorzwx/zh_mcp_server](https://github.com/Victorzwx/zh_mcp_server)。不要从 skill 执行其会打开 Chrome 的登录辅助脚本；登录必须由用户手动完成，Cookie 只放在本机：

```json
{
  "mcpServers": {
    "zh_mcp_server": {
      "command": "/absolute/path/to/zh_mcp_server/.venv/bin/python",
      "args": ["-m", "zh_mcp_server"]
    }
  }
}
```

当前上游代码实际将 `zhihu_cookies.json` 放在 Python 包目录附近；`json_path` 不能替代对实际 Cookie 文件位置的检查。登录后确认文件位于本机私有目录并限制权限，绝不将虚拟环境、Cookie 或登录输出提交到团队仓库。

## 工作流

1. 接收统一素材清单，核对标题、正文、图片顺序、署名、来源链接和目标知乎账号；标题和正文未确认时只生成预览。
2. 按共享会话规则 `attach` 已有专用浏览器，只读核对当前账号；需要登录、profile 被其他进程占用或账号不匹配时停止，不重置。
3. 直排并完成“正文图片保真检查”；未通过时只修复或切换适配器，不降级成纯文本发布。
4. 发布后回读知乎文章 URL、标题、作者、完整正文、全部图片顺序和封面状态；无法取得公开 URL 或仍是草稿时不算成功。
5. 回执记录标题、来源 URL、目标账号、文章 URL、封面状态和核验时间，不记录 Cookie 或令牌。

## 能力边界

`zh_mcp_server` 当前是知乎自动发文章服务，不提供可靠的“读取某账号最新文章/周报列表”工具。因此“从知乎搬运最新一期”必须由用户提供文章 URL/正文，或接入另一个经过验证的读取适配器。

## 去重与核验

以来源平台、来源内容 ID、目标知乎账号组成去重键；先查仓库外回执和目标账号文章列表，再决定发布或回读。跨平台任务只消费已有 manifest，不重新抓取来源；一端已成功时，知乎失败只重跑知乎。

安全配置以 [共享浏览器会话规则](../weekly-cross-platform-sync/references/browser-session.md) 为准；显式目标账号、专用 profile 核验和保留沙箱是写入前提。未知提交先回读，不重复提交。
