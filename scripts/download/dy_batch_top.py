#!/usr/bin/env python3
"""dy_batch_top.py — 批量下载指定博主 Top N 作品（按点赞排序）。

用法：
    python3 dy_batch_top.py /Users/linyv/Documents/自媒体/scripts/data/liyifan_173.json 30
"""
import asyncio, json, os, re, sys, subprocess, urllib.request

# 复用 dy_dl 的下载能力（headless Chrome + CDP 拦截）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dy_dl import (_capture_video_urls, _download, _merge, _has_video_stream,
                  _browser_alive, _start_browser)

DEFAULT_CDP = "http://127.0.0.1:9222"


def _safe_name(s: str, max_len: int = 50) -> str:
    s = (s or "").strip().replace("\n", " ")
    s = re.sub(r'[\\/:*?"<>|\r\n]', "_", s).strip()
    return s[:max_len] or "video"


async def _main():
    if len(sys.argv) < 2:
        sys.exit("用法: python3 dy_batch_top.py <meta.json> [N] [out_dir] [sort_by]\n"
                 "  sort_by: liked (默认) / time / collect")
    meta_path = sys.argv[1]
    top_n = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    out_root = sys.argv[3] if len(sys.argv) > 3 else "/Users/linyv/Documents/自媒体/downloads/批量"
    sort_by = sys.argv[4] if len(sys.argv) > 4 else "liked"

    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)
    items = meta["items"] if isinstance(meta, dict) and "items" in meta else meta
    sort_key = {"time": "create_time", "liked": "liked", "collect": "collect"}.get(sort_by, "liked")
    items = sorted(items, key=lambda x: x.get(sort_key, 0), reverse=True)[:top_n]
    nickname = (meta.get("nickname") if isinstance(meta, dict) else None) or os.path.splitext(os.path.basename(meta_path))[0]
    out_dir = out_root if "/" in out_root else os.path.join(out_root, nickname)
    os.makedirs(out_dir, exist_ok=True)

    # 确保 headless Chrome 启动
    if not _browser_alive(DEFAULT_CDP):
        _start_browser(
            chrome_bin="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            user_data_dir=os.path.expanduser("~/Library/Application Support/Google/Chrome"),
            profile_copy="/tmp/dy-profile", port=9222, app_name="Google Chrome",
            headless=True)

    print(f"→ 批量下载 Top {len(items)}（按 {sort_key}）")
    print(f"  输出目录: {out_dir}\n")
    ok, fail = 0, 0
    failed_ids = []  # (rank, aweme_id, desc) 供「登录态 detail API」重试
    for i, p in enumerate(items, 1):
        liked = p.get("liked", 0)
        desc = _safe_name(p.get("desc", ""), 50)
        iso = p.get("iso", "")
        out_path = os.path.join(out_dir, f"{i:02d}_likes{liked}_{iso}_{desc}.mp4")
        if p.get("duration_ms", 1) == 0:
            print(f"[{i}/{len(items)}] 跳过（图文作品，无视频流）: {desc[:30]}")
            continue
        print(f"[{i}/{len(items)}] 点赞={liked:>6}  {iso}  {desc[:30]}...")

        # 拦截/下载均为非确定性（video.m4s 偶尔 200+空 body）→ 失败自动重试 1 次
        done = False
        for attempt in (1, 2):
            if attempt == 2:
                print("  ↻ 重试（第 1 次结果无视频流）...")
            try:
                video, audio = await _capture_video_urls(DEFAULT_CDP, p["share_url"], timeout=70)
            except Exception as e:
                print(f"  ✗ 拦截失败: {e}")
                continue
            if not video:
                print("  ✗ 未能拦截到视频流")
                continue
            tmp_v = out_path + ".video.m4s"
            tmp_a = out_path + ".audio.m4s"
            try:
                _download(video, tmp_v)
                if audio: _download(audio, tmp_a)
                _merge(tmp_v, tmp_a if audio else tmp_v, out_path)
                for f in (tmp_v, tmp_a):
                    try: os.remove(f)
                    except OSError: pass
                # 金标准校验：文件里必须有视频流（大小检查对长视频不可靠）
                if not _has_video_stream(out_path):
                    try: os.remove(out_path)
                    except OSError: pass
                    print("  ✗ 结果只有音轨（视频流为空），删除坏文件")
                    continue
                sz = os.path.getsize(out_path) / 1024 / 1024
                print(f"  ✓ {sz:.1f} MB" + ("（重试成功）" if attempt == 2 else "") + "\n")
                ok += 1
                done = True
                break
            except Exception as e:
                print(f"  ✗ 下载/合并失败: {e}")
                continue
        if not done:
            print()
            fail += 1
            failed_ids.append((i, p.get("aweme_id", ""), p.get("desc", "")))

    print(f"\n完成：{ok} 成功 / {fail} 失败 / 共 {len(items)}")
    print(f"输出目录：{out_dir}")
    if failed_ids:
        # 落盘失败清单，供「登录态 detail API」修复路径直接读取
        fail_path = os.path.join(out_dir, "_failed.json")
        json.dump([{"rank": r, "aweme_id": a, "desc": d} for r, a, d in failed_ids],
                  open(fail_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"\n→ 失败清单已写入 {fail_path}（改用「登录态 detail API」路径重试）：")
        for rank, aid, d in failed_ids:
            print(f"  #{rank:02d} {aid}  {d[:40]}")


if __name__ == "__main__":
    asyncio.run(_main())
