#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抖音视频一键提取器（火山方舟）：默认免下载直传豆包 Seed，失败自动回退「下载后上传」。

主路径（default，全程不在本地落盘视频）:
1. 解析抖音分享链接（v.douyin.com 短链 / douyin.com/video/<id> 长链）→ aweme_id
2. headless Chrome（独立 profile，无登录态 cookie）CDP 匿名调 detail API，
   拿 detail 返回的合并版 MP4 直链候选（音画一体，非分离流）
3. 直链择优: 仅带 UA 的 Range 探针（模拟方舟服务器匿名拉流视角），
   取首个 HTTP 200/206 且体积 ≤50MB 的候选
4. 直传: 子进程调 extract_video_info.py --url <直链>（约 4 分钟出报告）

回退路径（直传任一环节失败自动触发; --force-download 可跳过直传直接走此路径）:
  dy_dl.py CDP 拦截下载完整 mp4 → extract_video_info.py 本地模式 Files API 上传提取

实测边界（2026-08-27）:
- 抖音直链是签名短时效链接（数小时内失效），必须"拿到即用"，不可缓存复用
- 同一视频不同时刻签出的直链可能被风控拦截（出现过裸 GET 403），故做多候选择优；
  全部候选失败时依赖回退路径
- 直传要求 ≤50MB 且支持匿名访问；上传上限 512MB，>512MB 回退时自动 ffmpeg 压缩
- 直传不做抽帧预处理，--fps 仅对回退的上传路径生效；直传重跑无法复用 file_id

依赖: requests、websockets、ffmpeg/ffprobe；dy_dl.py 已内置打包于本目录
     （源自 douyin-downloader 技能，本技能自包含，可整目录拷贝迁移）
用法:
  export ARK_API_KEY=<你的key>
  python3 dy_extract.py "https://v.douyin.com/xxxx/" [-o 输出目录] [--fps 1.0]
  python3 dy_extract.py "<链接>" --force-download   # 跳过直传，直接下载+上传
"""

import argparse
import asyncio
import glob
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
EXTRACT = os.path.join(SCRIPT_DIR, "extract_video_info.py")
CDP_DEFAULT = "http://127.0.0.1:9222"
PROBE_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36")
URL_LIMIT_BYTES = 50 * 1024 * 1024        # 方舟 video_url 输入上限
HOSTED_LIMIT_MB = 512                      # Files API 托管存储上限
TMP_PROFILE = "/tmp/dy-profile"
DETAIL_GROUPS = ("main", "h264", "bytevc1", "lowbr")


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def die(msg, code=1):
    print(f"✗ {msg}", file=sys.stderr)
    sys.exit(code)


def load_dy_module():
    """定位并导入 dy_dl.py，支持三种目录布局：

    1. 平铺: 同技能 scripts/ 下与本脚本同级（ark-video-extractor 布局）
    2. 上三角: ../download/dy_dl.py（blogger-distiller 的 download+extract 分层布局）
    3. 历史兼容: 兄弟技能 douyin-downloader
    """
    candidates = [
        os.path.join(SCRIPT_DIR, "dy_dl.py"),
        os.path.normpath(os.path.join(SCRIPT_DIR, "..", "download", "dy_dl.py")),
        os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "douyin-downloader", "scripts", "dy_dl.py")),
        os.path.expanduser("~/Documents/自媒体/.trae/skills/douyin-downloader/scripts/dy_dl.py"),
    ]
    for f in candidates:
        if os.path.isfile(f):
            d = os.path.dirname(os.path.abspath(f))
            if d not in sys.path:
                sys.path.insert(0, d)
            import dy_dl
            return dy_dl, d
    die("未找到 dy_dl.py（内置副本 / 上三角 download 层 / 兄弟技能 douyin-downloader 均不存在）")


def cdp_port(cdp_http):
    return urllib.parse.urlparse(cdp_http).port or 9222


def ensure_browser(dy_dl, cdp_http):
    """确保有可用的 CDP 浏览器；headless 启动，profile 独立、不含用户登录态。"""
    if dy_dl._browser_alive(cdp_http):
        return
    log("启动 headless Chrome（独立 profile /tmp/dy-profile，不含登录态）...")
    dy_dl._start_browser(
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        os.path.expanduser("~/Library/Application Support/Google/Chrome"),
        TMP_PROFILE,
        cdp_port(cdp_http), "Google Chrome", headless=True)
    if not dy_dl._browser_alive(cdp_http):
        die("浏览器已启动但 CDP 不可达")


def resolve_aweme_id(dy_dl, share_url):
    resolved = asyncio.run(dy_dl._resolve_short_url(share_url))
    m = re.search(r"/video/(\d+)|/note/(\d+)|aweme_id=(\d+)", resolved or share_url)
    if not m:
        die(f"未能从链接解析出 aweme_id: {resolved}")
    return next(g for g in m.groups() if g)


# ---------------------------------------------------------- 直链获取与探针

DETAIL_JS_TEMPLATE = """
(async ()=>{
  const p = new URLSearchParams({
    device_platform:'webapp',aid:'6383',channel:'channel_pc_web',
    pc_client_type:'1',version_code:'190500',version_name:'19.5.0',
    cookie_enabled:'true',browser_language:'zh-CN',browser_platform:'Win32',
    browser_name:'Edge',browser_online:'true',engine_name:'Blink',
    os_name:'Windows',os_version:'10',platform:'PC',
    screen_width:'1920',screen_height:'1080',
    aweme_id:'__ID__',request_source:'600',origin_type:'video_page'
  });
  try {
    const r = await fetch('/aweme/v1/web/aweme/detail/?' + p.toString(), {credentials:'include'});
    const j = await r.json();
    const d = j && j.aweme_detail;
    const v = (d && d.video) || {};
    const g = (o)=>((o && o.url_list) || []);
    return JSON.stringify({
      status: j && j.status_code,
      main: g(v.play_addr),
      h264: g(v.play_addr_h264),
      bytevc1: g(v.play_addr_bytevc1),
      lowbr: g(v.play_addr_lowbr)
    });
  } catch(e){ return JSON.stringify({err: e.message}); }
})()
"""


async def fetch_detail_candidates(cdp_http, aweme_id):
    """在 douyin.com 页面上下文调 detail API，返回有序直链候选 [(label,url)]。"""
    import websockets
    ver = json.loads(urllib.request.urlopen(f"{cdp_http}/json/version").read())
    async with websockets.connect(ver["webSocketDebuggerUrl"], max_size=None) as ws:
        mid = [500]

        async def send(method, params=None, session_id=None):
            mid[0] += 1
            payload = {"id": mid[0], "method": method, "params": params or {}}
            if session_id:
                payload["sessionId"] = session_id
            await ws.send(json.dumps(payload))
            while True:
                msg = json.loads(await ws.recv())
                if msg.get("id") == mid[0]:
                    return msg

        ctx_r = await send("Target.createBrowserContext")
        ctx = ctx_r["result"]["browserContextId"]
        tid = (await send("Target.createTarget", {
            "url": f"https://www.douyin.com/video/{aweme_id}",
            "browserContextId": ctx}))["result"]["targetId"]
        sess = (await send("Target.attachToTarget", {"targetId": tid, "flatten": True}))["result"]["sessionId"]
        await send("Page.addScriptToEvaluateOnNewDocument", {
            "source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});window.chrome={runtime:{}};"
        }, session_id=sess)
        await asyncio.sleep(6)
        r = await send("Runtime.evaluate", {
            "expression": DETAIL_JS_TEMPLATE.replace("__ID__", aweme_id),
            "awaitPromise": True, "returnByValue": True}, session_id=sess)
        val = ((r.get("result") or {}).get("result") or {}).get("value") or "{}"
        try:
            info = json.loads(val)
        except Exception:
            info = {}
        await send("Target.closeTarget", {"targetId": tid})
        await send("Target.disposeBrowserContext", {"browserContextId": ctx})

    log(f"detail API status={info.get('status')} err={info.get('err')}")
    ordered = []
    for idx in range(2):                       # 同组第二 url 常为另一 CDN 主机，作备选
        for grp in DETAIL_GROUPS:
            arr = info.get(grp) or []
            if len(arr) > idx:
                ordered.append((f"{grp}[{idx}]", arr[idx]))
    if not ordered:
        log("detail API 未返回直链")
    return ordered


def probe(url):
    """方舟视角裸拉流探测：仅 UA + Range。返回 (status, total_bytes_or_None)。"""
    req = urllib.request.Request(url, headers={
        "User-Agent": PROBE_UA,
        "Range": "bytes=0-2047",
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            resp.read()
            cr = resp.headers.get("Content-Range")     # 'bytes 0-2047/TOTAL'
            if cr and "/" in cr:
                try:
                    return resp.status, int(cr.rsplit("/", 1)[1])
                except ValueError:
                    pass
            cl = resp.headers.get("Content-Length")
            total = int(cl) if cl else None
            return resp.status, total
    except urllib.error.HTTPError as e:
        e.read()
        return e.code, None
    except Exception as ex:
        return str(ex)[:60], None


def choose_direct_url(candidates):
    """逐个探测，取首个匿名可拉且 ≤50MB 的直链（失败才会继续探下一个）。"""
    log("直链匿名拉流探针（仅 UA，无 Cookie/Referer —— 方舟服务器视角）:")
    for i, (label, u) in enumerate(candidates):
        st, total = probe(u)
        ok = st in (200, 206) and total is not None and 0 < total <= URL_LIMIT_BYTES
        size = f"{total / 1048576:.1f}MB" if total else "?"
        log(f"  {label}: status={st} total={size}{' <== 选它' if ok else ''}")
        if ok:
            return u
    return None


# ---------------------------------------------------------- 提取执行

def report_exists(outdir):
    return bool(glob.glob(os.path.join(outdir, "*.md")))


def run_direct(url, outdir, model=None):
    cmd = [sys.executable, EXTRACT, "--url", url, "-o", outdir]
    if model:
        cmd += ["--model", model]
    log(f"直传提取开始: {outdir}")
    rc = subprocess.call(cmd)
    ok = rc == 0 and report_exists(outdir)
    log(f"直传提取{'成功' if ok else f'失败(rc={rc})'}")
    return ok


def shrink_over_512mb(path):
    """超过 Files API 上限时压缩（优先保证能上传，可接受有损）。"""
    tmp = path + ".shrink.mp4"
    r = subprocess.run(
        ["ffmpeg", "-y", "-i", path, "-vf", "scale=-2:720",
         "-c:v", "libx264", "-crf", "26", "-c:a", "aac", "-b:a", "96k", tmp],
        capture_output=True, text=True)
    if r.returncode == 0 and os.path.isfile(tmp) and 0 < os.path.getsize(tmp) <= HOSTED_LIMIT_MB * 1048576:
        os.replace(tmp, path)
        return True
    if os.path.exists(tmp):
        os.remove(tmp)
    return False


def run_fallback(dy_dl, share_url, aweme_id, outdir, fps=1.0, model=None, dy_scripts=None):
    tmp_mp4 = os.path.join(tempfile.gettempdir(), f"dy_ark_fb_{aweme_id}.mp4")
    log("回退路径: CDP 拦截下载完整 mp4 ...")
    cmd = [sys.executable, os.path.join(dy_scripts, "dy_dl.py"), share_url,
           "-o", tmp_mp4, "--cdp", CDP_DEFAULT]
    rc = subprocess.call(cmd)
    if rc != 0 or not os.path.isfile(tmp_mp4) or os.path.getsize(tmp_mp4) == 0:
        die("回退下载失败（dy_dl.py 未产出文件），提取终止")
    if not dy_dl._has_video_stream(tmp_mp4):
        die("回退下载结果无视频流（只有音轨），提取终止")

    size_mb = os.path.getsize(tmp_mp4) / 1048576
    log(f"下载完成: {tmp_mp4} ({size_mb:.1f}MB)")
    if size_mb > HOSTED_LIMIT_MB:
        log(f"超过 {HOSTED_LIMIT_MB}MB 上限，ffmpeg 压缩 ...")
        if not shrink_over_512mb(tmp_mp4):
            die("压缩后仍超限，终止")

    cmd = [sys.executable, EXTRACT, tmp_mp4, "-o", outdir, "--fps", str(fps)]
    if model:
        cmd += ["--model", model]
    log(f"本地上传提取开始: {outdir}")
    rc = subprocess.call(cmd)
    ok = rc == 0 and report_exists(outdir)
    log(f"上传提取{'成功' if ok else f'失败(rc={rc})'}")
    if ok and os.path.isfile(tmp_mp4):
        os.remove(tmp_mp4)
        log("已清理临时视频文件")
    elif not ok:
        log(f"临时视频保留供排查: {tmp_mp4}")
    return ok


def main():
    ap = argparse.ArgumentParser(description="抖音链接一键提取（默认免下载直传，失败自动回退下载上传）")
    ap.add_argument("url", help="抖音分享链接（短链或 douyin.com/video/<id>）")
    ap.add_argument("-o", "--outdir", help="输出目录（默认: ./dy_<aweme_id>_extract_<时间戳>/）")
    ap.add_argument("--fps", type=float, default=1.0,
                    help="抽帧采样率（仅对回退的上传路径生效，默认 1.0）")
    ap.add_argument("--model", default=os.environ.get("ARK_MODEL"),
                    help="模型 ID（覆盖 ARK_MODEL 环境变量）")
    ap.add_argument("--force-download", action="store_true",
                    help="跳过直传，直接走「下载后上传」路径")
    args = ap.parse_args()

    if not os.environ.get("ARK_API_KEY"):
        die("缺少环境变量 ARK_API_KEY")
    if not re.match(r"^https?://", args.url):
        die("--url 必须是 http(s) 链接")
    if not os.path.isfile(EXTRACT):
        die(f"缺少提取脚本: {EXTRACT}")

    dy_dl, dy_scripts = load_dy_module()
    ensure_browser(dy_dl, CDP_DEFAULT)
    aweme_id = resolve_aweme_id(dy_dl, args.url)
    outdir = args.outdir or os.path.join(os.getcwd(), f"dy_{aweme_id}_extract_{time.strftime('%Y%m%d_%H%M%S')}")
    os.makedirs(outdir, exist_ok=True)

    chosen = None
    if not args.force_download:
        log(f"解析到 aweme_id={aweme_id}，获取合并 MP4 直链候选 ...")
        try:
            candidates = asyncio.run(fetch_detail_candidates(CDP_DEFAULT, aweme_id))
        except Exception as e:
            candidates = []
            log(f"取直链异常: {e}")
        if candidates:
            chosen = choose_direct_url(candidates)

    direct_ok = False
    if chosen:
        direct_ok = run_direct(chosen, outdir, model=args.model)
    elif not args.force_download:
        log("无可用直链（或已 --force-download），转入回退路径")

    if not direct_ok:
        if not run_fallback(dy_dl, args.url, aweme_id, outdir,
                            fps=args.fps, model=args.model, dy_scripts=dy_scripts):
            die("全部路径均失败")
    log(f"完成 ✓ 报告目录: {outdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
