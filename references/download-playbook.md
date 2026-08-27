# 收片手册（Download Playbook）· 单视频版

> 仅适用于本技能的单视频链接蒸馏。执行拆片（第 2 步）前，若触发直传失败回退需要本地下载/校验，先读本手册。

## 核心安全约束（必须遵守）

1. **下载绝不携带用户登录态 cookie**：headless Chrome 必须使用独立 profile（默认 `/tmp/dy-profile`），绝不能复用用户主 Chrome 的登录 profile 去请求视频流，否则可能触发风控导致封号。
2. **元数据查询可以在用户已登录的 Chrome 里做**（如直传失败需用 detail API 拿直链）：通过 `external_browser_evaluate` 调抖音 web API（`fetch(..., {credentials:'include'})`），属于正常浏览行为，安全。
3. **禁止在用户浏览器中执行任何点赞/关注/私信等写操作**。
4. **禁止伪造/估算点赞数、发布时间等数据**（缺数据就明说或换接口）。
5. 下载的视频流来自公开 CDN，仅需 `Referer: https://www.douyin.com/` 头，无需 cookie。

## 依赖

- Python 3.10+，`pip3 install --user websockets requests`
- ffmpeg 在 PATH 中
- macOS：Google Chrome

脚本位置：本技能 `scripts/download/dy_dl.py`（单视频下载）；直达提取走 `scripts/extract/dy_extract.py`。

## 主要路径：单视频免下载直传（推荐，技能默认）

`dy_extract.py` 已完成全部编排，一般无需手动下载：

```
解析分享链接 → headless Chrome（独立 profile，无登录态）匿名拿合并 MP4 直链
→ 直链匿名拉流探针（仅 UA，模拟方舟服务器视角）取首个 200/206 且 ≤50MB 的候选
→ 直传豆包（本地不落盘视频）
直传任一环节失败 → 自动回退「dy_dl.py 下载」+ 上传提取
```

- **直链是签名短时效链接**（数小时内失效），必须"拿到即用"，不可缓存复用。
- 直传要求 ≤50MB 且支持方舟服务器匿名访问；上传上限 512MB，>512MB 时回退路径自动 ffmpeg 压缩。
- **实测坑**：同一视频不同时刻签出的直链可能被方舟拉流超时（偶发 `Timeout while processing video_url`），与是否为新视频无关——协同脚本已做多候选择优，仍失败就**重跑一次**，第二次常成功。

## 辅助：单视频手动下载（备用）

```bash
cd <技能目录>/scripts/download
python3 dy_dl.py "<视频链接>" -o <输出.mp4>
```

短链自动解析；headless CDP 拦截 + fetch fallback，合并后自动 ffprobe 校验（无视频流则自动重试 + 报错）。

## headless Chrome 进程回收（🔒 强制，实测踩坑）

**症状**：脚本跑完后，本机 Chrome 卡住打不开窗口（或连不上外部 Chrome）。**根因**：脚本自动起的 headless Chrome（独立 profile `/tmp/dy-profile`，含 CDP 端口 9222）退出后残留未回收，占用 9222 端口、干扰用户正常 Chrome 弹出窗口。

**已内置自动回收**：`dy_dl.py`、`dy_extract.py` 都用 `_kill_headless_profile(profile)`——按 `user-data-dir` 特征精准 `pgrep` + `SIGKILL` 只杀本工具启动的 headless 实例，**绝不误杀用户正常 Chrome**；且用 try/finally 包裹，异常退出也回收。macOS 生效，Windows/Linux 跳过。

**⚠️ pgrep 选项坑（实测踩坑）**：`_kill_headless_profile` 里的 `pgrep -f` pattern **不能以 `--` 开头**（如 `--user-data-dir=...`），否则 macOS pgrep 会把它当成选项解析、报 `illegal option -- -`，**回收静默失效**，headless 继续残留卡住 Chrome。必须写成 `pgrep -f "user-data-dir=..."`（`-f` 是子串匹配，仍能命中完整命令行里的 `--user-data-dir=...`）。改完必须验证：跑一次脚本后 `pgrep -fl "dy-profile"` 应为空、`lsof -nP -iTCP:9222` 应无监听。

**手动兜底**（遇到残留仍卡窗口时）：
```bash
lsof -nP -iTCP:9222 -sTCP:LISTEN      # 看谁占 9222
pkill -9 -f "user-data-dir=/tmp/dy-profile"   # 只杀用该 profile 的 headless 残留
```
之后再正常启动 Chrome。若单视频分析中途遇到"连不上 Chrome / 弹不出窗口"，先跑上面两行，不要反复重连。

## 下载后必须校验：只有声音没有画面的坑（实测踩坑经验）

**症状**：下载"成功"（脚本没报错），但播放时只有声音没画面。

**根因**：headless CDP 拦截到的 `video.m4s` URL 下载返回了 **200 + 空 body**（HTTP 头成功所以 urllib 不报错），ffmpeg `-c copy` 把空 video 流和正常 audio 流合并，输出文件只剩音轨。

**⚠️ 大小检查不可靠（实测教训）**：短视频的音轨文件只有几百 KB，容易被"<1MB"阈值抓住；但**长视频光音轨就有十几 MB**。**金标准 = ffprobe 查 `codec_type=video`，大小只作弱信号**。

**⚠️ 无法在下载前预判**：拦截结果是非确定性的。故必须在下载后校验。

**失败修改（回退下载只有音轨时，用登录态 detail API 拿直链）**：

在用户 Chrome（已登录、已打开 douyin.com 页面的 tab）里 evaluate：

```javascript
(async () => {
  const id = '<aweme_id>';
  const p = new URLSearchParams({
    device_platform: 'webapp', aid: '6383', channel: 'channel_pc_web',
    pc_client_type: '1', version_code: '190500', version_name: '19.5.0',
    cookie_enabled: 'true', browser_language: 'zh-CN', browser_platform: 'Win32',
    browser_name: 'Edge', browser_online: 'true', engine_name: 'Blink',
    os_name: 'Windows', os_version: '10', platform: 'PC',
    screen_width: '1920', screen_height: '1080', aweme_id: id,
  });
  const r = await fetch('/aweme/v1/web/aweme/detail/?' + p.toString(), {credentials: 'include'});
  const j = await r.json();
  const d = j && j.aweme_detail;
  return JSON.stringify({
    id,
    desc: (d && d.desc || '').slice(0, 50),
    play_urls: (d && d.video && d.video.play_addr && d.video.play_addr.url_list) || [],
  });
})()
```

拿到 `play_urls[0]` 直链后用 curl 下载（CDN 仅需 Referer，**不传 cookie**，不违反安全约束）：

```bash
curl -sL \
  -H "Referer: https://www.douyin.com/" \
  -H "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36" \
  -o "<输出路径>.mp4" "<直链>"
```

**Cookie 使用说明**：只在 evaluate 调 detail API 时用到（GET 只读查询，无写副作用）；cookie 全程留在用户 Chrome 里，不进脚本、不进日志、不进下载文件；下载走 curl，不带任何 cookie。首次使用前向用户说明一次即可。

**注意**：`play_addr.url_list[0]` 是**已合并好的完整 mp4**（h264+aac），直接 curl 下载即可，无需 ffmpeg 合并。直链带签名有时效性（几小时内有效），拿到后立即下载。

## 常见问题速查

| 问题 | 处理 |
|------|------|
| CDP 拦截超时 | dy_dl.py 自带 fetch fallback；仍失败则重试一次 |
| 输出目录不存在 | 先 `mkdir -p` |
| 拦截到 URL 但下载失败 | 确认带 `Referer: https://www.douyin.com/` |
| 直传方舟超时 | 重跑一次 dy_extract.py（常见于直链随机被拉流超时） |