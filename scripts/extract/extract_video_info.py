#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
短视频多模态信息提取器（火山方舟版）

流程:
1. 本地视频通过 Files API 上传（方舟托管存储，最大 512MB，file_id 保留 7 天可复用）
2. 轮询等待视频预处理完成
3. 调用 Responses API（豆包视觉模型）提取 10 类核心信息（精简提示词约 1k token）：
   - 官方文档: max_output_tokens 必须显式传入（默认值很小），Agent 场景建议 >= 128000；
     本脚本默认 131072（模型最大回答 256k）
   - 默认单轮完成；若输出被截断可用 --split N 分轮重跑（file_id 复用免重新上传）
4. 流式接收结果，落盘: <创建时间精确到分钟>.md，如 20260827_0142.md（file_id/token 用量/截断状态输出到控制台）

依赖: pip3 install --user requests
环境变量:
  ARK_API_KEY   必填（火山方舟 API Key）
  ARK_MODEL   可选（默认 doubao-seed-2-1-pro-260628）
  ARK_BASE_URL 可选（默认 https://ark.cn-beijing.volces.com/api/v3）

用法:
  python3 extract_video_info.py <video.mp4> [-o 输出目录] [--fps 1.0]
  python3 extract_video_info.py --url <公网视频URL> [-o 输出目录]          # <50MB，跳过上传
  python3 extract_video_info.py --file-id <已上传的file_id> <video.mp4>    # 复用 7 天内的上传
"""

import argparse
import json
import math
import os
import re
import sys
import time

import requests

BASE_URL = os.environ.get("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
DEFAULT_MODEL = os.environ.get("ARK_MODEL", "doubao-seed-2-1-pro-260628")
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

MIME_MAP = {
    ".mp4": "video/mp4",
    ".m4v": "video/x-m4v",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo",
    ".flv": "video/x-flv",
}

HOSTED_LIMIT_MB = 512
POLL_INTERVAL = 5
POLL_TIMEOUT = 3600
# 官方文档（Agent 场景模型调用的正确姿势）: 回答被截断是因为 max_output_tokens 未显式传入
# 或值过小；不传时用模型默认值（约 4k，远小于全量档案所需）。模型最大回答 256k。
# 128k 覆盖绝大多数单轮档案输出（含思维链），且为官方推荐值。
DEFAULT_MAX_OUTPUT_TOKENS = 131072

CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def cn_to_int(s):
    """中文数字（1-99，'十'、'二十一'、'三十五'）转整数。"""
    if s == "十":
        return 10
    if "十" in s:
        a, _, b = s.partition("十")
        return (CN_NUM.get(a, 1) if a else 1) * 10 + (CN_NUM.get(b, 0) if b else 0)
    return CN_NUM.get(s, 0)


def auth_headers(api_key):
    return {"Authorization": f"Bearer {api_key}"}


# ---------------------------------------------------------------- 提示词分段

def split_prompt(text):
    """把 prompt.txt 拆成 (header, [(节号, 标题, 正文)])。节标题形如 '一、xxx'。"""
    header_lines, sections, cur = [], [], None
    for line in text.split("\n"):
        m = re.match(r"^([一二三四五六七八九十]+)、(.+)$", line.strip())
        if m and cn_to_int(m.group(1)) > 0:
            if cur:
                sections.append(cur)
            cur = (cn_to_int(m.group(1)), line.strip(), [])
        elif cur is None:
            header_lines.append(line)
        else:
            cur[2].append(line)
    if cur:
        sections.append(cur)
    return "\n".join(header_lines), sections


def make_groups(split, total):
    """分段方案。把 1..total 节均匀拆成 split 组；split<=1 时单轮全量。"""
    if split <= 1:
        return [(1, total)]
    step = math.ceil(total / split)
    return [(i, min(i + step - 1, total)) for i in range(1, total + 1, step)]


def build_part_prompt(header, sections, lo, hi, idx, total):
    """构造第 idx/total 轮的提示词：header + 指定节 + 本轮输出要求。"""
    parts = [header.strip(), ""]
    parts.append(f"【本轮任务说明】")
    parts.append(f"这是同一个视频分段提取任务的第 {idx}/{total} 轮。")
    parts.append(f"本轮你只需要输出第 {lo} 节到第 {hi} 节的内容，其他章节由其他轮次完成，本轮禁止输出。")
    parts.append("")
    for num, title, body in sections:
        if lo <= num <= hi:
            parts.append(title)
            if body:
                parts.append("\n".join(body))
            parts.append("")
    parts.append("【本轮输出要求】")
    parts.append("- 直接输出上述各节内容。每节标题必须用三级标题格式单独成行（如「### 二、完整口播转写」），相邻节之间用 --- 分隔，条目一律用无序列表逐条列出，严格遵守 header 中的输出排版规范 A-F。")
    parts.append("- 同时严格遵循 header 中的提取原则：只提取不分析、证据分级、不擅自补全、保留时间戳、不重复输出。")
    return "\n".join(parts)


# ---------------------------------------------------------------- API 调用

def upload_video(path, api_key, fps):
    """Files API 上传本地视频，返回 file_id。"""
    size_mb = os.path.getsize(path) / 1024 / 1024
    if size_mb > HOSTED_LIMIT_MB:
        log(f"警告: 文件 {size_mb:.0f}MB 超过方舟托管存储 {HOSTED_LIMIT_MB}MB 上限，仍尝试上传（如失败请压缩，或配置 TOS）")
    mime = MIME_MAP.get(os.path.splitext(path)[1].lower(), "video/mp4")
    data = {
        "purpose": "user_data",
        "preprocess_configs[video][fps]": str(fps),
    }
    with open(path, "rb") as f:
        r = requests.post(
            f"{BASE_URL}/files",
            headers=auth_headers(api_key),
            data=data,
            files={"file": (os.path.basename(path), f, mime)},
            timeout=3600,
        )
    if r.status_code != 200:
        raise RuntimeError(f"上传失败 HTTP {r.status_code}: {r.text[:500]}")
    file_id = r.json()["id"]
    log(f"上传成功: {file_id}")
    return file_id


def wait_processing(file_id, api_key, timeout=POLL_TIMEOUT):
    """轮询文件状态直到预处理完成。"""
    start = time.time()
    while True:
        r = requests.get(
            f"{BASE_URL}/files/{file_id}",
            headers=auth_headers(api_key),
            timeout=60,
        )
        if r.status_code != 200:
            raise RuntimeError(f"查询文件状态失败 HTTP {r.status_code}: {r.text[:300]}")
        info = r.json()
        status = info.get("status", "")
        if status == "processing":
            if time.time() - start > timeout:
                raise TimeoutError(f"视频预处理超时（>{timeout}s），file_id={file_id}")
            log("预处理中...")
            time.sleep(POLL_INTERVAL)
            continue
        if status in ("failed", "error"):
            raise RuntimeError(f"文件处理失败: {json.dumps(info, ensure_ascii=False)[:500]}")
        log(f"预处理完成: status={status}")
        return info


def extract_text_from_response(resp):
    """从 Responses API 响应对象中提取输出文本。"""
    parts = []
    for item in resp.get("output", []) or []:
        for c in item.get("content", []) or []:
            if c.get("type") in ("output_text", "text") and "text" in c:
                parts.append(c["text"])
    return "\n".join(parts)


def run_extraction(api_key, model, content, stream=True, max_tokens=None, label=""):
    """调用 Responses API。返回 (完整文本, 终态响应对象或None, 收到的事件类型集合)。"""
    body = {"model": model, "input": [{"role": "user", "content": content}]}
    if max_tokens:
        body["max_output_tokens"] = max_tokens

    if not stream:
        body["stream"] = False
        r = requests.post(
            f"{BASE_URL}/responses",
            headers={**auth_headers(api_key), "Content-Type": "application/json"},
            json=body,
            timeout=7200,
        )
        if r.status_code != 200:
            raise RuntimeError(f"请求失败 HTTP {r.status_code}: {r.text[:800]}")
        resp = r.json()
        return extract_text_from_response(resp), resp, set()

    body["stream"] = True
    text_parts = []
    final_response = None
    event_types = set()
    last_progress = time.time()
    with requests.post(
        f"{BASE_URL}/responses",
        headers={**auth_headers(api_key), "Content-Type": "application/json"},
        json=body,
        stream=True,
        timeout=7200,
    ) as r:
        if r.status_code != 200:
            raise RuntimeError(f"请求失败 HTTP {r.status_code}: {r.text[:800]}")
        # 注意: 必须以 bytes 迭代再 utf-8 解码；decode_unicode=True 会把跨网络块的
        # 多字节字符切坏，导致整行 JSON 解析失败、丢失输出内容。
        for raw in r.iter_lines():
            if not raw:
                continue
            line = raw.decode("utf-8", errors="replace")
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                ev = json.loads(payload)
            except json.JSONDecodeError:
                continue
            etype = ev.get("type", "")
            if etype:
                event_types.add(etype)
            if etype == "response.output_text.delta":
                text_parts.append(ev.get("delta", ""))
                if time.time() - last_progress > 30:
                    log(f"{label}已接收 {sum(len(p) for p in text_parts)} 字符...")
                    last_progress = time.time()
            elif etype in ("response.completed", "response.incomplete", "response.failed"):
                if isinstance(ev.get("response"), dict):
                    final_response = ev["response"]
            elif etype in ("error",):
                raise RuntimeError(f"API 错误事件: {payload[:800]}")
    full = "".join(text_parts)
    if not full and final_response:
        full = extract_text_from_response(final_response)
    return full, final_response, event_types


def response_is_truncated(final_response, text):
    """终态事件 status=incomplete 视为截断；无终态时以输出为空兜底。"""
    if final_response:
        status = (final_response.get("status") or "").lower()
        if status == "incomplete":
            return True
        if status == "completed":
            return False
    return not bool((text or "").strip())


# ---------------------------------------------------------------- 输出整理

def save_outputs(outdir, parts_text):
    """parts_text: [(label, text, truncated)]。合并写 report.md，返回 (路径, 截断轮次)。"""
    os.makedirs(outdir, exist_ok=True)

    report_lines = []
    for i, (label, text, truncated) in enumerate(parts_text):
        if len(parts_text) > 1:
            report_lines.append(f"\n\n<!-- ===== 第 {i + 1} 轮提取（{label}）===== -->\n")
        report_lines.append(text)
    report = "\n".join(report_lines).strip() + "\n"
    report_path = os.path.join(outdir, f"{time.strftime('%Y%m%d_%H%M')}.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)

    truncated_parts = [i + 1 for i, (_, _, t) in enumerate(parts_text) if t]
    return report_path, truncated_parts


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser(description="短视频多模态信息提取器（火山方舟）")
    ap.add_argument("video", nargs="?", help="本地视频文件路径")
    ap.add_argument("--url", help="公网可访问的视频 URL（<50MB），跳过上传")
    ap.add_argument("--file-id", help="复用已上传的方舟 file_id（7 天有效），跳过上传")
    ap.add_argument("-o", "--outdir", help="输出目录（默认: <视频目录>/<视频名>_extract_<时间戳>/）")
    ap.add_argument("--model", default=DEFAULT_MODEL, help=f"模型 ID（默认 {DEFAULT_MODEL}）")
    ap.add_argument("--fps", type=float, default=1.0, help="视频抽帧采样率，默认 1.0；信息密集可提高到 2")
    ap.add_argument("--split", type=int, default=1, help="提示词分段轮数，默认 1（单轮全量）；输出被截断时才需要调高")
    ap.add_argument("--api-key", default=os.environ.get("ARK_API_KEY"), help="API Key（默认读环境变量 ARK_API_KEY）")
    ap.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_OUTPUT_TOKENS, help=f"单轮最大输出 token 数（默认 {DEFAULT_MAX_OUTPUT_TOKENS}）")
    ap.add_argument("--no-stream", action="store_true", help="禁用流式输出（排障用）")
    ap.add_argument("--prompt-file", default=os.path.join(SCRIPT_DIR, "prompt.txt"), help="提取提示词文件路径")
    args = ap.parse_args()

    if not args.api_key:
        sys.exit("错误: 缺少 API Key。请设置环境变量 ARK_API_KEY 或使用 --api-key")
    if not os.path.isfile(args.prompt_file):
        sys.exit(f"错误: 提示词文件不存在: {args.prompt_file}")
    with open(args.prompt_file, "r", encoding="utf-8") as f:
        prompt_full = f.read()

    header, sections = split_prompt(prompt_full)
    if not sections:
        sys.exit("错误: 提示词文件中未找到任何节标题（形如「一、xxx」）")
    groups = make_groups(args.split, len(sections))
    # 过滤掉超出实际节数的组
    max_sec = sections[-1][0]
    groups = [(lo, hi) for lo, hi in groups if lo <= max_sec]
    log(f"提示词共 {len(sections)} 节，将分 {len(groups)} 轮提取: {groups}")

    # ---- 准备视频输入
    if args.url:
        if not re.match(r"^https?://", args.url):
            sys.exit("错误: --url 必须是 http(s) 链接")
        log(f"使用视频 URL: {args.url}")
        video_content = {"type": "input_video", "video_url": args.url}
        outdir = args.outdir or os.path.join(os.getcwd(), f"extract_url_{time.strftime('%Y%m%d_%H%M%S')}")
    else:
        if not args.video or not os.path.isfile(args.video):
            sys.exit("错误: 视频文件不存在（或使用 --url / --file-id 传入）")
        size_mb = os.path.getsize(args.video) / 1024 / 1024
        log(f"视频: {args.video} ({size_mb:.1f}MB)")

        if args.file_id:
            file_id = args.file_id
            log(f"复用已上传文件: {file_id}")
        else:
            file_id = upload_video(args.video, args.api_key, args.fps)
            wait_processing(file_id, args.api_key)
        video_content = {"type": "input_video", "file_id": file_id}
        base = os.path.splitext(os.path.basename(args.video))[0]
        outdir = args.outdir or os.path.join(
            os.path.dirname(os.path.abspath(args.video)),
            f"{base}_extract_{time.strftime('%Y%m%d_%H%M%S')}",
        )

    # ---- 分轮提取
    parts_text = []
    usage_total = {"input_tokens": 0, "output_tokens": 0}
    for i, (lo, hi) in enumerate(groups):
        part_prompt = build_part_prompt(header, sections, lo, hi, i + 1, len(groups))
        content = [video_content, {"type": "input_text", "text": part_prompt}]
        log(f"第 {i + 1}/{len(groups)} 轮（第 {lo}-{hi} 节）开始，长视频可能需要数分钟...")
        try:
            text, final_response, event_types = run_extraction(
                args.api_key, args.model, content,
                stream=not args.no_stream, max_tokens=args.max_tokens,
                label=f"[轮{i + 1}] ",
            )
        except requests.exceptions.ReadTimeout:
            sys.exit("错误: 读取响应超时。可尝试分段处理，或减小 --fps 降低输入长度")
        if not text.strip():
            sys.exit(f"错误: 第 {i + 1} 轮未返回任何内容。请检查模型 ID 是否支持视频理解")

        truncated = response_is_truncated(final_response, text)
        if final_response:
            usage = final_response.get("usage") or {}
            for k in ("input_tokens", "output_tokens"):
                usage_total[k] = usage_total.get(k, 0) + (usage.get(k) or 0)
        parts_text.append((f"第 {lo}-{hi} 节", text, truncated))
        log(f"第 {i + 1} 轮完成: {len(text)} 字符, 截断={truncated}")

    # ---- 落盘
    log(f"全部轮次完成。保存到: {outdir}")
    report_path, truncated_parts = save_outputs(outdir, parts_text)

    print()
    print("========== 完成 ==========")
    print(f"报告: {report_path}")
    if video_content.get("file_id"):
        print(f"file_id: {video_content['file_id']}（7 天内可 --file-id 复用免上传）")
    print(f"token 用量: {json.dumps(usage_total, ensure_ascii=False)}")
    if truncated_parts:
        print(f"⚠ 截断轮次: {truncated_parts} — 请用更高 --split 值重跑")
    return 0


if __name__ == "__main__":
    sys.exit(main())
