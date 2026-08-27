# 收片手册（Download Playbook）

> 执行收片阶段前必读。

## 核心安全约束（必须遵守）

1. **下载绝不携带用户登录态 cookie**：headless Chrome 必须使用独立 profile（默认 `/tmp/dy-profile`），绝不能复用用户主 Chrome 的登录 profile 去请求视频流，否则可能触发风控导致封号。
2. **元数据抓取可以在用户已登录的 Chrome 里做**：通过 `external_browser_evaluate` 调用抖音 web API（`fetch(..., {credentials:'include'})`），属于正常浏览行为，安全。
3. **禁止在用户浏览器中执行任何点赞/关注/私信等写操作**。
4. **禁止伪造/估算点赞数、发布时间等元数据**（缺数据就明说或换接口）。
5. 下载的视频流来自公开 CDN，仅需 `Referer: https://www.douyin.com/` 头，无需 cookie。

## 依赖

- Python 3.10+，`pip3 install --user websockets`
- ffmpeg 在 PATH 中
- macOS：Google Chrome

脚本位置：本技能 `scripts/download/`（`dy_dl.py` 单视频、`dy_batch_top.py` 批量）。

## 第一步：抓取博主全部作品元数据

### 方式 A（推荐）：在用户已登录的 Chrome 里调 web API

前置：调用 `browser_connect_plugin` 确认外部 Chrome 连通，用 `external_browser_navigate` 打开抖音（如已登录）。然后在抖音页面上下文执行 evaluate：

```javascript
(async () => {
  const uid = '<sec_uid>';  // 从博主主页 URL 提取: douyin.com/user/<sec_uid>
  const base = 'https://www.douyin.com/aweme/v1/web/aweme/post/';
  const common = {
    device_platform: 'webapp', aid: '6383', channel: 'channel_pc_web',
    pc_client_type: '1', version_code: '190500', version_name: '19.5.0',
    cookie_enabled: 'true', browser_language: 'zh-CN', browser_platform: 'Win32',
    browser_name: 'Edge', browser_online: 'true', engine_name: 'Blink',
    os_name: 'Windows', os_version: '10', platform: 'PC',
    screen_width: '1920', screen_height: '1080',
  };
  const all = [];
  let cursor = 0;
  for (let page = 0; page < 30; page++) {
    const p = new URLSearchParams({...common, sec_user_id: uid, max_cursor: cursor, count: '20'});
    const r = await fetch(base + '?' + p.toString(), {credentials: 'include'});
    if (!r.ok) break;
    const j = await r.json();
    for (const a of (j.aweme_list || [])) {
      all.push({
        aweme_id: a.aweme_id,
        desc: (a.desc || '').slice(0, 200),
        create_time: a.create_time,
        liked: a.statistics?.digg_count || 0,
        comment: a.statistics?.comment_count || 0,
        collect: a.statistics?.collect_count || 0,
        share: a.statistics?.share_count || 0,
        duration_ms: a.video?.duration || 0,
        share_url: 'https://www.douyin.com/video/' + a.aweme_id,
        iso: new Date(a.create_time*1000).toISOString().slice(0,10),
      });
    }
    if (!j.has_more || !j.max_cursor) break;
    cursor = j.max_cursor;
  }
  return JSON.stringify({total: all.length, items: all});
})()
```

注：`play_count` 在 web 端 API 恒为 0，属正常现象，不要伪造。

**sec_uid 获取**：直接从用户提供的博主主页 URL 提取（形如 `douyin.com/user/<sec_uid>`）。本工具不做按名字搜索解析。

**数据落地**：evaluate 拿到 JSON 后无法直接写文件（沙箱无 fs）。直接 `return JSON.stringify(...)`，大输出会截断并生成完整 log 文件（`/var/folders/.../evaluate-<时间戳>.log`），shell 复制该文件再用 Python 重写成标准格式。详见「元数据落盘」。**不要用 `<a download>` 或本地 HTTP server 接收（均实测失败）**。

### 方式 B（无登录态 fallback）：iesdouyin 移动版

用 headless Chrome（iPhone UA）打开 `https://www.iesdouyin.com/share/user/<sec_uid>`，从 `.user-post-cover` 节点的 React fiber props 提取 `productionUrl / likedCount / desc`。**限制**：无发布时间、无评论/收藏数据，点赞为精确值。

### 元数据落盘：两种情形（实测踩坑经验）

Exec 沙箱无 `fs`，evaluate 拿到的大 JSON 无法直接写本地文件。**不要**尝试以下两条路径（均已实测失败）：

1. ~~`<a download>` 触发浏览器下载~~ — 文件不落 `~/Downloads`（下载路径不可靠）
2. ~~起本地 HTTP server 接收 POST~~ — evaluate 里 `await fetch(...)` 长阻塞极易触发 Bridge 30s 超时，且端口可能被占用

让 evaluate 直接 `return JSON.stringify(data)`，根据输出是否被截断分两种情形：

**情形 A：输出被截断（有 log 路径，输出 > 约 3 万字符）**

工具结果会提示 `Full output (N bytes): /var/folders/.../evaluate-<时间戳>.log`——这个 log 文件就是**完整无损的 JSON**：

```bash
# 1. 从 evaluate 输出里找到 log 路径
# 2. shell 复制 + 用 Python 验证并重写成标准元数据格式
cp "/var/folders/.../evaluate-XXXX.log" <运行目录>/tmp_items.json
python3 -c "
import json
items = json.load(open('<运行目录>/tmp_items.json'))
dump = {
  'fetched_at': '...', 'nickname': '...', 'sec_uid': '...',
  'total': len(items), 'items': items,
}
json.dump(dump, open('<运行目录>/meta.json','w',encoding='utf-8'),
          ensure_ascii=False, indent=2)
"
rm <运行目录>/tmp_items.json
```

**情形 B：输出未截断（无 log 路径，JSON 已完整进入对话上下文）**

此时完整 JSON 就在 evaluate 的工具结果里，**直接将 JSON 内容保存为本地文件**，再用 Python 验证 + 重写成标准格式。

若 JSON 过大写 Write 也吃力，改用分批策略：把 items 数组切片多次 evaluate，每次返回一段（控制每段 < 3 万字符），在 shell 端拼接。

**注意**：分页抓取时 `count=20` 是单页上限，`has_more=1` 要继续翻页。如果 evaluate 循环提前 break（比如只拿到 20 条但博主实际更多），先检查返回的 total 数量级是否与主页作品数一致，不一致则重跑或分批抓。

### 元数据 JSON 标准结构

```json
{
  "fetched_at": "ISO时间",
  "nickname": "博主名",
  "sec_uid": "MS4wLjAB...",
  "total": 173,
  "items": [
    {
      "aweme_id": "7639371669090612411",
      "desc": "标题...",
      "create_time": 1778679823,
      "iso": "2026-05-13",
      "liked": 96548,
      "comment": 2681,
      "collect": 84062,
      "share": 17029,
      "duration_ms": 418562,
      "share_url": "https://www.douyin.com/video/7639371669090612411"
    }
  ]
}
```

## 第二步：向用户确认排序与数量

作品数 > 15 时用 AskUserQuestion 问两个问题：
- **排序方式**：最多点赞（推荐）/ 最新时间 / 最多收藏（基于已抓取的真实数据给出 Top3 预览）
- **下载数量**：5 / 10 / 20 / 全部（注明全部的预估耗时，约每个 30 秒）

## 第三步：批量下载

```bash
cd <技能目录>/scripts/download
python3 dy_batch_top.py <运行目录>/meta.json <数量> <运行目录>/videos
```

- 自动按点赞降序取前 N 个
- 文件名格式：`{序号:02d}_likes{点赞数}_{日期}_{标题前50字}.mp4`
- 每个约 30 秒，30 个约 15 分钟；长任务用非阻塞 RunCommand + CheckCommandStatus 轮询进度
- 通过 `ls <输出目录> | wc -l` 检查完成数
- 完成后必须跑「下载后校验」的两道检查（文件大小 + ffprobe 视频流），异常文件走「失败修复」

单视频手动下载（备用）：`python3 dy_dl.py "<视频链接>" -o <输出.mp4>`（短链自动解析；headless CDP 拦截 + fetch fallback，合并后自动 ffprobe 校验）。

## 下载后必须校验：只有声音没有画面的坑（实测踩坑经验）

**症状**：下载"成功"（脚本没报错），但播放时只有声音没画面。

**根因**：headless CDP 拦截到的 `video.m4s` URL 下载返回了 **200 + 空 body**（HTTP 头成功所以 urllib 不报错），ffmpeg `-c copy` 把空 video 流和正常 audio 流合并，输出文件只剩音轨。

**⚠️ 大小检查不可靠（实测教训）**：短视频的音轨文件只有几百 KB，容易被"<1MB"阈值抓住；但**长视频光音轨就有十几 MB**（如 12 分钟教学视频 18MB），大小看起来正常，只有 ffprobe 查流才能识破。**金标准 = ffprobe 查 `codec_type=video`，大小只作弱信号**。

**⚠️ 无法在下载前预判**：拦截结果是非确定性的（同一视频有时成功有时失败），与视频新旧、长短无关。故必须在下载后校验。

**脚本已内置自动化**：
- `dy_batch_top.py` 每个视频下载后自动 ffprobe 校验；无视频流→**自动重试 1 次**，仍失败→删除坏文件、计入失败清单
- 失败清单自动写入 `<输出目录>/_failed.json`（rank/aweme_id/desc）
- `duration_ms == 0` 的图文作品自动跳过

**🔒 修复闭环（强制要求，不得跳过）**：批量下载结束后——
1. 读 `_failed.json`（或全目录 ffprobe 体检），只要有失败/音轨文件，**必须立即自动执行下面的 detail API 修复**，不要只报告失败就结束
2. 全部完成后 ffprobe 复检输出目录确认 0 坏文件
3. 该修复路径实测战绩：萝卜乔乔 4/4、非哥黑科技 3/3 + 7/7、全量修复 34/34，**成功率 100%**

## 失败修复：用户 Chrome 登录态 detail API 拿直链（实测兜底方案）

批量下载中 headless 独立 profile 拦截失败的视频（老视频命中率较高），用**用户已登录 Chrome 的 evaluate** 调 `aweme/detail` API 拿 `play_addr.url_list` 直链，再 curl 下载（CDN 仅需 Referer，**不传 cookie**，不违反安全约束）。

**先判类型**：`duration_ms == 0` 的是图文/动态作品，没有视频流，直接跳过并告知用户，不要重试。

**Cookie 使用说明**：只在 evaluate 调 detail API 时用到（GET 只读查询，无写副作用，无频控风险）；cookie 全程留在用户 Chrome 里，不进脚本、不进日志、不进下载文件；下载走 curl，不带任何 cookie。首次使用前向用户说明一次即可。

**重要**：`play_addr.url_list[0]` 是**已合并好的完整 mp4**（h264+aac），不是分离的 video/audio 流——直接 curl 下载即可，无需 ffmpeg 合并。

**第一步**：在用户 Chrome（已登录、已打开 douyin.com 页面的 tab）里 evaluate：

```javascript
(async () => {
  const ids = ['<aweme_id_1>', '<aweme_id_2>'];  // 失败的 id
  const out = [];
  for (const id of ids) {
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
    if (!d) { out.push({id, err: 'status=' + (j && j.status_code)}); continue; }
    out.push({
      id,
      desc: (d.desc || '').slice(0, 50),
      play_urls: (d.video && d.video.play_addr && d.video.play_addr.url_list) || [],
    });
  }
  return JSON.stringify(out);
})()
```

**第二步**：shell curl 下载（url_list[0] 即直链）：

```bash
curl -sL \
  -H "Referer: https://www.douyin.com/" \
  -H "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36" \
  -o "<输出路径>.mp4" "<直链>"
```

**第三步**：`ffprobe -v error -show_entries format=duration` 验证时长与元数据 `duration_ms` 一致。

注意：直链带签名有时效性（几小时内有效），拿到后立即下载，不要存下来隔天用。重下覆盖时，若新文件名与旧文件名不完全一致，旧文件会残留——先删同序号前缀的旧文件。

**手动兜底体检**（对历史目录批量检查）：

```bash
cd <输出目录>
for f in *.mp4; do
  n_video=$(ffprobe -v error -show_entries stream=codec_type -of csv=p=0 "$f" | grep -c video)
  [ "$n_video" -eq 0 ] && echo "异常(无视频流): $f"
done
```

## 大文件压缩：超过 50MB 的视频必须压到 ≤50MB（ffmpeg）

**规则（🔒 强制）**：每次批量下载/修复流程结束后，发现文件 > 50MB（51,200,000 bytes）的，**必须自动**用 ffmpeg 重编码压到 ≤50MB——这是流程的一部分，不要停下来问用户。压缩后替换原文件，无需保留高清原版。

**触发时机**：① 批量下载完成扫描输出目录；② detail API 修复闭环完成后扫描（**此路径最常命中**：直链是最高画质源）；③ 任何历史目录体检发现 >50MB 文件。

**批量压缩命令**（可直接执行的完整脚本）：

```bash
cd <输出目录>
mkdir -p /tmp/dy_compress
for f in *.mp4; do
  sz=$(stat -f%z "$f")
  if [ "$sz" -gt 51200000 ]; then
    dur=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$f")
    vbr=$(python3 -c "print(max(int(44*1024*1024*8/$dur/1000) - 96, 150))")
    ffmpeg -y -i "$f" -c:v libx264 -b:v ${vbr}k -maxrate $((vbr*12/10))k \
      -bufsize $((vbr*15/10))k -preset medium -c:a aac -b:a 96k "/tmp/dy_compress/tmp.mp4" \
      -loglevel error
    nsz=$(stat -f%z /tmp/dy_compress/tmp.mp4)
    if [ "$nsz" -gt 1000000 ] && [ "$nsz" -lt 51200000 ]; then
      mv /tmp/dy_compress/tmp.mp4 "$f"
      echo "✓ $(echo "$nsz/1048576" | bc -l | cut -c1-5) MB  $f"
    else
      rm -f /tmp/dy_compress/tmp.mp4
      echo "✗ 压缩失败/仍超限: $f（重试：vbr×0.85）"
    fi
  fi
done
```

**要点（实测校准）**：
- **一轮就压到位的关键参数**：目标 **44MB**、音频 **96k**、`maxrate = vbr×1.2`、`bufsize = vbr×1.5`、`vbr` 下限 150k。实测 11/11 全部一次成功（42-45MB）
- **不要用** 目标 48MB + 音频 128k + `maxrate = vbr×2`——长视频会因码率上浮和音频占比超限（实测 26 个里 11 个失败）
- 压完必须 `stat` 复查 < 50MB；万一仍超，vbr×0.85 重压一次
- 文件名保持不变（先输出 `/tmp/dy_compress/tmp.mp4`，成功后 mv 覆盖）；失败不覆盖
- 短视频（<3 分钟）1080p 一般到不了 50MB，此规则主要命中长视频 / detail API 高码率源
- ffmpeg 编码耗时约为视频时长的 30%-100%，大文件多时后台执行、定期查进度

## 常见问题速查

| 问题 | 处理 |
|------|------|
| CDP 拦截超时 | dy_dl.py 自带 fetch fallback；仍失败则重试一次 |
| 输出目录不存在 | 先 `mkdir -p` |
| 磁盘占用 | 提前告知用户估算（长视频约 10-45 MB/个） |
| 拦截到 URL 但下载失败 | 确认带 `Referer: https://www.douyin.com/` |
| 批量中途失败 | 脚本继续下一个，最后汇总 ok/fail 数；走修复闭环 |
