#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
유튜브 반자동화 도구 (yt.py)
링크만 넣으면 채널 인기순 데이터 / 자막 원고 / 썸네일을 자동 수집한다.

사용법:
    python yt.py channel <채널URL> [--scan 50] [--top 25] [--min-duration 180] [--project 이름]
    python yt.py transcript <영상URL...> [--lang ko] [--project 이름]
    python yt.py thumbs <채널URL> [--top 3] [--project 이름]

의존성: yt-dlp (python -m yt_dlp 로 호출)
"""

import sys
import io
import os
import re
import json
import argparse
import subprocess
from datetime import datetime

# ── Windows 콘솔 UTF-8 강제 ──────────────────────────────
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE_DIR, "출력")
os.makedirs(OUT_DIR, exist_ok=True)

YTDLP = [sys.executable, "-m", "yt_dlp"]

SHORTS_MIN_SEC = 180  # 이보다 짧은 영상은 쇼츠로 간주해 순위 집계에서 제외
CHARS_PER_MIN_KO = 378  # 한국어 내레이션 평균 낭독 속도 (분당 글자수). 실측(372~384자/분) 평균값


def out_dir_for(project):
    """프로젝트명이 있으면 출력/<프로젝트>/, 없으면 출력/ 그대로. 결과 파일 덮어쓰기 방지용."""
    d = os.path.join(OUT_DIR, project) if project else OUT_DIR
    os.makedirs(d, exist_ok=True)
    return d


# ── 공통 유틸 ────────────────────────────────────────────
def human(n):
    """조회수를 읽기 쉽게 (12,345 / 1.2만 / 3.4억)."""
    if n is None:
        return "?"
    if n >= 100_000_000:
        return f"{n/100_000_000:.1f}억"
    if n >= 10_000:
        return f"{n/10_000:.1f}만"
    return f"{n:,}"


def fmt_date(s):
    if not s or len(s) != 8:
        return "?"
    return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"


def fmt_dur(sec):
    if not sec:
        return "?"
    sec = int(sec)
    h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def run_ytdlp(args):
    """yt-dlp 실행, stdout 텍스트 반환."""
    proc = subprocess.run(
        YTDLP + args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.stdout, proc.stderr


# ── 1) 채널 인기순 분석 ──────────────────────────────────
def cmd_channel(url, scan, top, project, min_duration):
    print(f"⏳ 채널 스캔 중... 최근 {scan}개 영상의 조회수를 수집합니다 (약 {scan}초 소요)\n")

    stdout, stderr = run_ytdlp([
        "--dump-json",
        "--playlist-end", str(scan),
        "--ignore-errors",
        "--no-warnings",
        url,
    ])

    videos = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        videos.append({
            "title": d.get("title", ""),
            "views": d.get("view_count"),
            "date": d.get("upload_date", ""),
            "duration": d.get("duration"),
            "url": d.get("webpage_url") or f"https://youtu.be/{d.get('id','')}",
            "id": d.get("id", ""),
        })

    if not videos:
        print("❌ 영상을 가져오지 못했습니다. URL을 확인해 주세요.")
        print("   (채널이면 .../@이름/videos 형태를 권장)")
        if stderr:
            print("\n[stderr]\n" + stderr[:800])
        return

    # 쇼츠(min_duration 미만) 제외 — 조회수 스케일이 달라 median을 왜곡시킴
    scanned_total = len(videos)
    videos = [v for v in videos if not v["duration"] or v["duration"] >= min_duration]
    shorts_excluded = scanned_total - len(videos)

    if not videos:
        print(f"❌ 롱폼 영상이 없습니다 (쇼츠 {shorts_excluded}개만 발견). --min-duration을 낮춰 보세요.")
        return

    # 조회수 내림차순 정렬 (None 은 뒤로)
    videos.sort(key=lambda v: (v["views"] is None, -(v["views"] or 0)))

    valid_views = [v["views"] for v in videos if v["views"]]
    median = sorted(valid_views)[len(valid_views) // 2] if valid_views else 0

    top_videos = videos[:top]

    # ── 마크다운 출력 ──
    lines = []
    lines.append(f"# 📊 채널 인기순 분석 결과\n")
    lines.append(f"- 소스: {url}")
    lines.append(f"- 스캔한 영상: {scanned_total}개 (쇼츠 {shorts_excluded}개 제외) / 표시: 상위 {len(top_videos)}개")
    lines.append(f"- 중간 조회수(median): **{human(median)}회**  ← 이 채널의 '평타' 기준 (롱폼만 집계)")
    lines.append(f"- 생성 시각: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
    lines.append("| # | 조회수 | 배수 | 업로드 | 길이 | 제목 |")
    lines.append("|---|--------|------|--------|------|------|")

    for i, v in enumerate(top_videos, 1):
        ratio = ""
        flag = ""
        if v["views"] and median:
            r = v["views"] / median
            ratio = f"{r:.1f}x"
            if r >= 2.0:
                flag = " 🔥"  # 대박작
        lines.append(
            f"| {i} | {human(v['views'])} | {ratio}{flag} | "
            f"{fmt_date(v['date'])} | {fmt_dur(v['duration'])} | {v['title']} |"
        )

    lines.append("\n> 🔥 = 평타 대비 2배 이상 터진 '대박작' (제목·주제 공식을 여기서 뽑아냄)\n")
    lines.append("## 영상 링크 (자막 뽑을 때 사용)\n")
    for i, v in enumerate(top_videos, 1):
        lines.append(f"{i}. {v['url']}  — {v['title']}")

    md = "\n".join(lines)

    # 파일 저장
    out_dir = out_dir_for(project)
    md_path = os.path.join(out_dir, "채널분석.md")
    json_path = os.path.join(out_dir, "채널분석.json")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({"source": url, "median": median, "videos": top_videos}, f, ensure_ascii=False, indent=2)

    print(md)
    print(f"\n💾 저장됨: {md_path}")


# ── 2) 자막 원고 추출 ────────────────────────────────────
def clean_vtt(raw):
    """VTT 자막 → 중복 제거된 순수 텍스트."""
    lines = raw.splitlines()
    out = []
    seen_last = None
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        if ln.startswith("WEBVTT") or ln.startswith("Kind:") or ln.startswith("Language:"):
            continue
        if "-->" in ln:
            continue
        if ln.isdigit():
            continue
        # 인라인 타임스탬프/태그 제거 <00:00:00.000><c> 등
        ln = re.sub(r"<[^>]+>", "", ln).strip()
        if not ln:
            continue
        if ln == seen_last:
            continue
        seen_last = ln
        out.append(ln)
    return "\n".join(out)


def fetch_one_transcript(url, lang, out_dir):
    tmp_base = os.path.join(out_dir, "_sub_tmp")
    # 이전 임시파일 정리
    for f in os.listdir(out_dir):
        if f.startswith("_sub_tmp"):
            try:
                os.remove(os.path.join(out_dir, f))
            except OSError:
                pass

    # 수동 자막 우선, 없으면 자동 자막
    run_ytdlp([
        "--skip-download",
        "--write-subs",
        "--write-auto-subs",
        "--sub-langs", f"{lang}.*,{lang}",
        "--sub-format", "vtt",
        "--output", tmp_base,
        "--no-warnings",
        url,
    ])

    # 생성된 vtt 찾기
    vtt = None
    for f in os.listdir(out_dir):
        if f.startswith("_sub_tmp") and f.endswith(".vtt"):
            vtt = os.path.join(out_dir, f)
            break
    if not vtt:
        return None
    with open(vtt, encoding="utf-8", errors="replace") as fh:
        raw = fh.read()
    # 남은 임시 자막파일 전부 정리 (언어 변형 여러 개 받아질 수 있음)
    for f in os.listdir(out_dir):
        if f.startswith("_sub_tmp"):
            try:
                os.remove(os.path.join(out_dir, f))
            except OSError:
                pass
    return clean_vtt(raw)


def cmd_transcript(urls, lang, project):
    out_dir = out_dir_for(project)
    all_text = []
    total_chars = 0
    for i, url in enumerate(urls, 1):
        print(f"⏳ [{i}/{len(urls)}] 자막 추출 중: {url}")
        text = fetch_one_transcript(url, lang, out_dir)
        if not text:
            print(f"   ⚠️ '{lang}' 자막을 찾지 못했습니다. (다른 언어 코드 --lang en 시도 가능)")
            continue
        wc = len(text.replace("\n", "").replace(" ", ""))
        total_chars += wc
        print(f"   ✅ 완료 — 약 {wc:,}자")
        all_text.append(f"\n\n===== 영상 {i}: {url} =====\n{text}")

    if not all_text:
        print("\n❌ 자막을 하나도 못 가져왔습니다.")
        return

    combined = "".join(all_text)
    path = os.path.join(out_dir, "자막원고.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(combined)
    avg_chars = total_chars // len(all_text)
    avg_minutes = avg_chars / CHARS_PER_MIN_KO
    print(f"\n💾 저장됨: {path}")
    print(f"   총 {len(urls)}개 영상 중 {len(all_text)}개 성공")
    print(f"   평균 {avg_chars:,}자/편 ≈ 낭독 시 약 {avg_minutes:.1f}분 (기준: 분당 {CHARS_PER_MIN_KO}자)")
    print(f"   ⚠️ 자동 생성 자막이므로 STT 오타가 섞여 있을 수 있음 — 문맥상 이상한 단어는 오타로 간주하고 문체 학습에서 무시할 것")


# ── 3) 썸네일 다운로드 ───────────────────────────────────
def cmd_thumbs(url, top, project):
    print(f"⏳ 상위 {top}개 영상 썸네일 다운로드 중...\n")
    # 먼저 인기순으로 상위 영상 id 수집
    stdout, _ = run_ytdlp([
        "--dump-json", "--playlist-end", str(max(top * 3, 15)),
        "--ignore-errors", "--no-warnings", url,
    ])
    vids = []
    for line in stdout.splitlines():
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        vids.append((d.get("view_count") or 0, d.get("id"), d.get("title", "")))
    vids.sort(reverse=True)

    out_dir = out_dir_for(project)
    thumb_dir = os.path.join(out_dir, "썸네일")
    os.makedirs(thumb_dir, exist_ok=True)
    got = 0
    for views, vid, title in vids[:top]:
        if not vid:
            continue
        out_base = os.path.join(thumb_dir, f"{got+1:02d}_{vid}")
        run_ytdlp([
            "--skip-download", "--write-thumbnail",
            "--convert-thumbnails", "jpg",
            "--output", out_base,
            "--no-warnings",
            f"https://youtu.be/{vid}",
        ])
        got += 1
        print(f"   ✅ {got:02d}. [{human(views)}회] {title[:40]}")
    print(f"\n💾 저장 폴더: {thumb_dir}  ({got}개)")


# ── 엔트리 ───────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description="유튜브 반자동화 도구")
    sub = p.add_subparsers(dest="cmd", required=True)

    pc = sub.add_parser("channel", help="채널 인기순 분석")
    pc.add_argument("url")
    pc.add_argument("--scan", type=int, default=50, help="조회수 수집할 최근 영상 수 (기본 50)")
    pc.add_argument("--top", type=int, default=25, help="결과에 표시할 상위 영상 수 (기본 25)")
    pc.add_argument("--min-duration", type=int, default=SHORTS_MIN_SEC, help=f"이 초(sec) 미만은 쇼츠로 간주해 제외 (기본 {SHORTS_MIN_SEC})")
    pc.add_argument("--project", default=None, help="출력/<프로젝트명>/ 폴더에 저장 (미지정 시 출력/ 바로 아래, 덮어쓰기 주의)")

    pt = sub.add_parser("transcript", help="자막 원고 추출")
    pt.add_argument("urls", nargs="+")
    pt.add_argument("--lang", default="ko", help="자막 언어 코드 (기본 ko)")
    pt.add_argument("--project", default=None, help="출력/<프로젝트명>/ 폴더에 저장")

    pth = sub.add_parser("thumbs", help="썸네일 다운로드")
    pth.add_argument("url")
    pth.add_argument("--top", type=int, default=3)
    pth.add_argument("--project", default=None, help="출력/<프로젝트명>/ 폴더에 저장")

    args = p.parse_args()
    if args.cmd == "channel":
        cmd_channel(args.url, args.scan, args.top, args.project, args.min_duration)
    elif args.cmd == "transcript":
        cmd_transcript(args.urls, args.lang, args.project)
    elif args.cmd == "thumbs":
        cmd_thumbs(args.url, args.top, args.project)


if __name__ == "__main__":
    main()
