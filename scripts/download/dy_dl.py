#!/usr/bin/env python3
"""dy_dl.py — 通过 Chrome DevTools Protocol 拦截视频流，下载抖音分享链接对应的视频。

用法：
    # 本机已运行 Chrome 且开启了远程调试端口（默认 9222）
    python3 dy_dl.py "https://v.douyin.com/XXXX/"

    # 指定 CDP 端点（任意 Chromium / Edge / Brave 等浏览器，或远程机器）
    python3 dy_dl.py "<url>" --cdp http://127.0.0.1:9222
    python3 dy_dl.py "<url>" --cdp http://192.168.1.10:9222

    # 自动启动/接管 Chrome（macOS 默认行为）
    python3 dy_dl.py "<url>" --auto-launch

    # 无头模式（不需要 GUI；远程/服务器/CI 场景）
    python3 dy_dl.py "<url>" --auto-launch --headless

    # 自定义浏览器二进制和 user-data-dir
    python3 dy_dl.py "<url>" --auto-launch \\
        --chrome-bin /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome \\
        --user-data-dir "$HOME/Library/Application Support/Google/Chrome" \\
        --profile-copy /tmp/dy-profile \\
        --cdp-port 9333

依赖：
    - Python 3.10+，已 pip3 install --user websockets
    - 系统有 ffmpeg
    - macOS / Linux（Windows 需自行调整 Chrome 路径）
"""
import argparse, asyncio, json, os, platform, re, shutil, signal, subprocess, sys, time, urllib.parse, urllib.request

DEFAULT_CDP = "http://127.0.0.1:9222"


async def _resolve_short_url(url: str) -> str:
    """跟踪 v.douyin.com 短链的重定向，返回最终 URL。"""
    import asyncio as _a
    loop = _a.get_running_loop()
    def _do():
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        # 不自动 follow redirect
        opener = urllib.request.build_opener(urllib.request.HTTPRedirectHandler)
        class NoRedir(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        o = urllib.request.build_opener(NoRedir)
        try:
            r = o.open(req, timeout=15)
            return r.geturl()
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308):
                return e.headers.get("Location", url)
            return url
        except Exception:
            return url
    return await loop.run_in_executor(None, _do)


# -------- Chrome 进程与端口探测 --------
def _chrome_listen_pid(port: int) -> int | None:
    """返回监听指定 TCP 端口的浏览器 PID（若有）。"""
    try:
        out = subprocess.check_output(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"]
        ).decode().strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    if not out:
        return None
    try:
        return int(out.splitlines()[0])
    except ValueError:
        return None


def _browser_alive(cdp: str) -> bool:
    try:
        urllib.request.urlopen(cdp + "/json/version", timeout=3).read()
        return True
    except Exception:
        return False


# -------- 启动 / 接管 Chrome（仅 macOS；其它平台可由外部脚本启动后用 --cdp 接入） --------
def _osascript_quit(app_name: str):
    """通过 AppleScript 让指定浏览器安全退出（标签页会恢复）。"""
    if platform.system() != "Darwin":
        return
    subprocess.run(["osascript", "-e", f'tell application "{app_name}" to quit'],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _wait_no_chrome(timeout=10):
    for _ in range(int(timeout / 0.5)):
        if subprocess.run(["pgrep", "-f", "Chrome"], stdout=subprocess.DEVNULL).returncode != 0:
            return
        time.sleep(0.5)


def _kill_port_listener(port: int):
    pid = _chrome_listen_pid(port)
    if pid:
        try: os.kill(pid, signal.SIGKILL)
        except ProcessLookupError: pass


def _kill_headless_profile(profile_copy: str):
    """结束由本工具启动的 headless Chrome（按 --user-data-dir 特征精准匹配）。

    只杀目标 profile（默认 /tmp/dy-profile）的实例，绝不影响用户正常 Chrome。
    脚本退出时必须回收自己启动的 headless，否则残留进程会占用 9222 端口，
    甚至导致用户无法正常打开/弹出 Chrome 窗口（实测踩坑）。
    """
    if platform.system() != "Darwin":
        return
    try:
        # 注意：pattern 不能以 "--" 开头，否则 macOS pgrep 会当成选项解析报
        # "illegal option -- -"，导致回收失效、headless 残留卡住用户 Chrome。
        # 去掉前导 --（-f 为子串匹配），仍能命中命令行里的 --user-data-dir=...
        out = subprocess.check_output(
            ["pgrep", "-f", f"user-data-dir={profile_copy}"]
        ).decode().strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return
    for pid in out.splitlines():
        try: os.kill(int(pid), signal.SIGKILL)
        except (ProcessLookupError, ValueError): pass


def _ensure_profile_copy(src_dir: str, copy_dir: str):
    """Chromium 强制要求 --user-data-dir 指向非默认目录：复制一份过去。"""
    default_dir = os.path.join(src_dir, "Default")
    if not os.path.isdir(default_dir):
        sys.exit(f"✗ 找不到 Default profile: {default_dir}")
    if os.path.isdir(os.path.join(copy_dir, "Default")):
        return
    if os.path.exists(copy_dir):
        shutil.rmtree(copy_dir, ignore_errors=True)
    os.makedirs(copy_dir, exist_ok=True)
    shutil.copytree(default_dir, os.path.join(copy_dir, "Default"), symlinks=True)
    ls = os.path.join(src_dir, "Local State")
    if os.path.exists(ls):
        shutil.copy2(ls, os.path.join(copy_dir, "Local State"))


def _start_browser(chrome_bin: str, user_data_dir: str, profile_copy: str,
                   port: int, app_name: str = "Google Chrome", headless: bool = False):
    """启动浏览器（headless 支持）。"""
    if not headless:
        _ensure_profile_copy(user_data_dir, profile_copy)
        # 清掉 SingletonLock
        for f in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
            p = os.path.join(profile_copy, f)
            if os.path.exists(p):
                try: os.remove(p)
                except OSError: pass
        # GUI 模式：先杀掉旧实例，让标签恢复
        _kill_port_listener(port)
        _osascript_quit(app_name)
        _wait_no_chrome()
    else:
        # headless 模式：直接杀监听端口的旧实例，无需 osascript
        _kill_port_listener(port)
    log_path = "/tmp/dy-chrome.log"
    args = [
        chrome_bin,
        f"--user-data-dir={profile_copy}",
        f"--remote-debugging-port={port}",
        "--remote-allow-origins=*",
        "--no-first-run", "--no-default-browser-check", "--no-sandbox",
        "--disable-features=Translate,BackForwardCache,AcceptCHFrame",
        "--disable-blink-features=AutomationControlled",
        "--profile-directory=Default",
    ]
    if headless:
        # headless=new 是当前推荐的新 headless 模式（保留完整浏览器功能）
        args += ["--headless=new", "--disable-gpu", "--hide-scrollbars",
                 "--mute-audio", "--window-size=1920,1080"]
    with open(log_path, "ab") as fp:
        subprocess.Popen(args, stdout=fp, stderr=subprocess.STDOUT, start_new_session=True)
    for _ in range(60):
        if _chrome_listen_pid(port):
            return
        time.sleep(0.25)
    raise RuntimeError(f"浏览器未能在 {port} 启动，查看 {log_path}")


# -------- CDP 抓取 --------
async def _capture_video_urls(cdp_http: str, share_url: str, timeout: int = 60):
    import websockets
    ver = json.loads(urllib.request.urlopen(f"{cdp_http}/json/version").read())
    browser_ws = ver["webSocketDebuggerUrl"]
    captured = []
    fetch_result = None
    async with websockets.connect(browser_ws, max_size=None) as ws:
        mid = [0]
        async def send(method, params=None, sessionId=None):
            mid[0] += 1
            payload = {"id": mid[0], "method": method, "params": params or {}}
            if sessionId: payload["sessionId"] = sessionId
            await ws.send(json.dumps(payload))
            while True:
                m = json.loads(await ws.recv())
                if m.get("id") == mid[0]:
                    return m
        ctx_r = await send("Target.createBrowserContext")
        ctx = ctx_r["result"]["browserContextId"]
        t_r = await send("Target.createTarget", {"url": "about:blank", "browserContextId": ctx})
        tid = t_r["result"]["targetId"]
        a_r = await send("Target.attachToTarget", {"targetId": tid, "flatten": True})
        sess = a_r["result"]["sessionId"]
        await send("Network.enable", sessionId=sess)
        await send("Page.enable", sessionId=sess)
        await send("Runtime.enable", sessionId=sess)
        # 抹掉自动化标记 + 注入无害的 chrome.runtime，避免被风控识别为 headless bot
        await send("Page.addScriptToEvaluateOnNewDocument", {
            "source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
                      "window.chrome={runtime:{}};"
                      "Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});"
                      "Object.defineProperty(navigator,'languages',{get:()=>['zh-CN','zh','en']});"
        }, sessionId=sess)
        await send("Page.navigate", {"url": share_url}, sessionId=sess)

        async def listener():
            nonlocal fetch_result
            try:
                while True:
                    raw = await ws.recv()
                    m = json.loads(raw)
                    if m.get("sessionId") != sess: continue
                    if m.get("method") == "Network.responseReceived":
                        url = m["params"]["response"].get("url", "")
                        if re.search(r"(douyinvod\.com|media-video|media-audio|tos-cn|\.mp4|\.m3u8|/play/)", url):
                            captured.append({"url": url,
                                             "status": m["params"]["response"].get("status")})
                    elif m.get("method") == "Runtime.evaluate" and m.get("id") == mid[0] + 1:
                        pass  # ignore our own fetch result echo
            except asyncio.CancelledError: pass
            except Exception: pass

        # 解析短链以拿到真实 aweme_id（用于 fallback）
        resolved = await _resolve_short_url(share_url)
        m_id = re.search(r"/video/(\d+)|/note/(\d+)|aweme_id=(\d+)", resolved or share_url)
        aweme_id = (m_id.group(1) or m_id.group(2) or m_id.group(3)) if m_id else None

        task = asyncio.create_task(listener())
        await asyncio.sleep(min(timeout, 25))
        # 触发播放
        try:
            await send("Runtime.evaluate", {
                "expression": "document.querySelectorAll('video').forEach(v=>{v.muted=true;v.play().catch(()=>{});});",
                "returnByValue": True}, sessionId=sess)
        except Exception: pass
        # 第一轮监听结束；如未拿到，关闭 listener 释放 ws
        task.cancel()
        await asyncio.sleep(2)
        # Fallback：直接在页面里 fetch aweme/detail
        if not captured and aweme_id:
            fetch_js = f"""
            (async ()=>{{
              const p = new URLSearchParams({{
                device_platform:'webapp',aid:'6383',channel:'channel_pc_web',
                pc_client_type:'1',version_code:'190500',version_name:'19.5.0',
                cookie_enabled:'true',browser_language:'zh-CN',browser_platform:'Win32',
                browser_name:'Edge',browser_online:'true',engine_name:'Blink',
                os_name:'Windows',os_version:'10',platform:'PC',
                screen_width:'1920',screen_height:'1080',
                aweme_id:'{aweme_id}',request_source:'600',origin_type:'video_page'
              }});
              try {{
                const r = await fetch('/aweme/v1/web/aweme/detail/?' + p.toString(), {{credentials:'include'}});
                const j = await r.json();
                const detail = j && j.aweme_detail;
                const v = detail && detail.video;
                const urls = (v && v.play_addr && v.play_addr.url_list) || [];
                const al = (v && v.play_addr_lowbr && v.play_addr_lowbr.url_list) || [];
                return JSON.stringify({{video:urls, audio:al, status:j && j.status_code, msg:j && j.status_msg}});
              }} catch(e){{return 'ERR:'+e.message;}}
            }})()
            """
            try:
                r = await send("Runtime.evaluate", {
                    "expression": fetch_js, "awaitPromise": True,
                    "returnByValue": True}, sessionId=sess)
                val = (r.get("result", {}).get("result", {}) or {}).get("value", "")
                fetch_result = json.loads(val) if val else None
            except Exception as e:
                fetch_result = {"err": str(e)}
        try:
            await send("Target.closeTarget", {"targetId": tid})
            await send("Target.disposeBrowserContext", {"browserContextId": ctx})
        except Exception: pass

    video = next((c["url"] for c in captured if "media-video" in c["url"] or "/video/tos/" in c["url"]), None)
    audio = next((c["url"] for c in captured if "media-audio" in c["url"] or "audio-und" in c["url"]), None)
    # 去重，保留 br 最高的（URL 末尾 br/bt 参数）
    if video:
        return video, audio
    if fetch_result:
        vs = fetch_result.get("video") or []
        aus = fetch_result.get("audio") or []
        if vs:
            return vs[0], (aus[0] if aus else None)
    return None, None


# -------- 下载 / 合并 --------
def _download(url: str, path: str):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
        "Referer": "https://www.douyin.com/",
    })
    with urllib.request.urlopen(req, timeout=60) as r, open(path, "wb") as f:
        while True:
            chunk = r.read(64 * 1024)
            if not chunk: break
            f.write(chunk)


def _safe_title(share_url: str) -> str:
    try:
        req = urllib.request.Request(share_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            html = r.read().decode("utf-8", "replace")
        m = re.search(r"<title>([^<]+)</title>", html)
        if m:
            return re.sub(r'[\\/:*?"<>|]', "_", m.group(1)).strip()[:80]
    except Exception: pass
    return "douyin_" + time.strftime("%Y%m%d_%H%M%S")


def _merge(video_path: str, audio_path: str, out_path: str):
    if audio_path == video_path:
        # 仅视频流，没有音频时直接复制
        shutil.copyfile(video_path, out_path)
        return
    r = subprocess.run(
        ["ffmpeg", "-y", "-i", video_path, "-i", audio_path,
         "-c", "copy", "-shortest", out_path],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg 失败：" + r.stderr[-800:])


def _has_video_stream(path: str) -> bool:
    """ffprobe 检查文件是否含视频流。

    拦截到的 video.m4s 偶尔返回 200+空 body，合并后只剩音轨——
    文件大小对此不可靠（长视频光音轨就有十几 MB），必须查流。
    """
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
             "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=30)
        return "video" in r.stdout.split()
    except Exception:
        return False


# -------- 主流程 --------
def main():
    ap = argparse.ArgumentParser(description="通过 CDP 下载抖音视频")
    ap.add_argument("url", help="抖音分享链接")
    ap.add_argument("-o", "--output", help="输出文件路径")
    ap.add_argument("--cdp", default=DEFAULT_CDP,
                    help="CDP HTTP 端点（默认 http://127.0.0.1:9222）")
    ap.add_argument("--timeout", type=int, default=60, help="CDP 监听秒数")
    ap.add_argument("--auto-launch", action="store_true",
                    help="如 CDP 未就绪，自动启动浏览器（macOS）")
    ap.add_argument("--chrome-bin", default="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                    help="浏览器可执行路径")
    ap.add_argument("--user-data-dir",
                    default=os.path.expanduser("~/Library/Application Support/Google/Chrome"),
                    help="原始 user-data-dir（用于复制 profile）")
    ap.add_argument("--profile-copy", default="/tmp/dy-profile",
                    help="复制 profile 的目标目录（须不同于默认值）")
    ap.add_argument("--cdp-port", type=int, default=9222,
                    help="--auto-launch 时使用的远程调试端口")
    ap.add_argument("--app-name", default="Google Chrome",
                    help="osascript 要退出的应用名（headless 下无效）")
    ap.add_argument("--headless", action="store_true", default=True,
                    help="使用 headless=new 模式（默认开启；--no-headless 用 GUI）")
    ap.add_argument("--no-headless", dest="headless", action="store_false",
                    help="禁用无头模式，改用 GUI Chrome（需要本机有 GUI）")
    args = ap.parse_args()

    launched_headless = False
    try:
        if not _browser_alive(args.cdp):
            if not args.auto_launch:
                sys.exit(f"✗ CDP 端点 {args.cdp} 无响应；可用 --auto-launch 自动启动浏览器")
            print("→ 启动浏览器...", flush=True)
            _start_browser(args.chrome_bin, args.user_data_dir, args.profile_copy,
                           args.cdp_port, args.app_name, headless=args.headless)
            launched_headless = bool(args.headless)
            # 重定向后续 CDP 调用到正确端口
            if args.cdp == DEFAULT_CDP:
                args.cdp = f"http://127.0.0.1:{args.cdp_port}"
            if not _browser_alive(args.cdp):
                sys.exit("✗ 浏览器已启动但 CDP 仍不可达")

        print(f"→ CDP {args.cdp} 抓取视频流...", flush=True)
        video, audio = asyncio.run(_capture_video_urls(args.cdp, args.url, timeout=args.timeout))
        if not video:
            sys.exit("✗ 未能拦截到视频流（页面可能未登录或视频受限）")
        print(f"  video: {video[:90]}...", flush=True)
        if audio:
            print(f"  audio: {audio[:90]}...", flush=True)

        title = _safe_title(args.url)
        out_dir = os.path.expanduser("~/Documents/自媒体/downloads")
        os.makedirs(out_dir, exist_ok=True)
        out_path = args.output or os.path.join(out_dir, f"{title}.mp4")
        if not out_path.endswith(".mp4"):
            out_path += ".mp4"

        tmp_v = out_path + ".video.m4s"
        tmp_a = out_path + ".audio.m4s"
        print("→ 下载...", flush=True)
        _download(video, tmp_v)
        if audio:
            _download(audio, tmp_a)
        print("→ 合并...", flush=True)
        _merge(tmp_v, tmp_a if audio else tmp_v, out_path)
        for p in (tmp_v, tmp_a):
            try: os.remove(p)
            except OSError: pass
        if not _has_video_stream(out_path):
            try: os.remove(out_path)
            except OSError: pass
            sys.exit("✗ 下载结果只有音轨（视频流为空）——拦截到的视频 URL 无效，"
                     "请重试；仍失败则改用「登录态 detail API」路径")
        print(f"✓ 完成：{out_path}  ({os.path.getsize(out_path)/1024/1024:.1f} MB)")
    finally:
        # 回收本次启动的 headless Chrome，防止残留进程占用 9222 并卡住用户正常 Chrome
        if launched_headless:
            _kill_headless_profile(args.profile_copy)


if __name__ == "__main__":
    main()