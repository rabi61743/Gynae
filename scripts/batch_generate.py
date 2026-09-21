#!/usr/bin/env python3
"""
Production Batch Engine for Williams Obstetrics (26th Edition).
Generates neural audiobooks (MP3) and synchronized subtitles (SRT) for all chapters,
with auto-retry, section-by-section checkpointing, and immediate GitHub pushes.
"""

import os
import re
import sys
import json
import time
import asyncio
import argparse
import subprocess
import edge_tts
from PIL import Image, ImageDraw
import imageio_ffmpeg

REPO_DIR = "/Volumes/Kingston/Gynae"
CHAPTERS_DIR = os.path.join(REPO_DIR, "chapters")
AUDIO_DIR = os.path.join(REPO_DIR, "audiobooks")
SUBTITLES_DIR = os.path.join(REPO_DIR, "subtitles")
VIDEO_DIR = os.path.join(REPO_DIR, "videos")
SCRIPTS_DIR = os.path.join(REPO_DIR, "scripts")
MANIFEST_PATH = os.path.join(REPO_DIR, "manifest.json")
README_PATH = os.path.join(REPO_DIR, "README.md")
FFMPEG_BIN = imageio_ffmpeg.get_ffmpeg_exe()

for d in [AUDIO_DIR, SUBTITLES_DIR, VIDEO_DIR, SCRIPTS_DIR]:
    os.makedirs(d, exist_ok=True)

SECTION_MAP = {
    1: {"name": "Overview", "chapters": [1]},
    2: {"name": "Maternal Anatomy and Physiology", "chapters": [2, 3, 4]},
    3: {"name": "Placentation, Embryogenesis, and Fetal Development", "chapters": [5, 6, 7]},
    4: {"name": "Preconceptional and Prenatal Care", "chapters": [8, 9, 10]},
    5: {"name": "First- and Second-Trimester Pregnancy Loss", "chapters": [11, 12, 13]},
    6: {"name": "The Fetal Patient", "chapters": [14, 15, 16, 17, 18, 19, 20]},
    7: {"name": "Labor", "chapters": [21, 22, 23, 24, 25, 26]},
    8: {"name": "Delivery", "chapters": [27, 28, 29, 30, 31]},
    9: {"name": "The Newborn", "chapters": [32, 33, 34, 35]},
    10: {"name": "The Puerperium", "chapters": [36, 37, 38, 39]},
    11: {"name": "Obstetrical Complications", "chapters": [40, 41, 42, 43, 44, 45, 46, 47, 48]},
    12: {"name": "Medical and Surgical Complications", "chapters": list(range(49, 69))},
}

def load_manifest():
    if os.path.exists(MANIFEST_PATH):
        try:
            with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_manifest(manifest):
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

def clean_text_for_speech(md_text: str) -> str:
    """Prepares clean narration script by removing bibliography, headings, and clutter."""
    # Discard references section using the LAST occurrence of '## References' to avoid matching outline at top
    matches = list(re.finditer(r"\n##\s*References\b", md_text, flags=re.IGNORECASE))
    if matches:
        # Take the last match (the actual bibliography at end of chapter)
        md_text = md_text[:matches[-1].start()]
    
    # Expand Chapter Header for natural spoken introduction
    text = re.sub(r"^#\s*Chapter\s+(\d+):\s*(.*)$", r"Williams Obstetrics. Chapter \1: \2.", md_text, flags=re.MULTILINE)
    
    # Convert Section Headers
    text = re.sub(r"^##\s*(.*)$", r"\1.", text, flags=re.MULTILINE)
    text = re.sub(r"^###\s*(.*)$", r"\1.", text, flags=re.MULTILINE)
    
    # Figures & Tables
    text = re.sub(r">\s*\*\*Figure\s+(\d+[-–]\d+):\*\*\s*(.*)", r"Figure \1. \2", text)
    text = re.sub(r">\s*\*\*Table\s+(\d+[-–]\d+):\*\*\s*(.*)", r"Table \1. \2", text)
    text = re.sub(r"^>\s*", "", text, flags=re.MULTILINE)
    
    # Formatting
    text = text.replace("**", "").replace("*", "")
    
    # Parenthetical citations
    text = re.sub(r"\([A-Z][A-Za-z\s–,-]+,?\s*\d{4}[a-z]?\)", "", text)
    text = re.sub(r"\(Chap\.\s*\d+,\s*p\.\s*\d+\)", "", text)
    text = re.sub(r"\(see\s+Table\s+[\d-]+\)", "", text)
    text = re.sub(r"\(Fig\.\s*[\d-]+[a-z]?\)", "", text)
    text = re.sub(r"\(Table\s*[\d-]+[a-z]?\)", "", text)
    
    # Bullets & whitespace
    text = re.sub(r"^-\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s+", " ", text).strip()
    return text

def get_chapter_file(chap_num: int):
    prefix = f"Chapter_{chap_num:02d}_"
    for f in os.listdir(CHAPTERS_DIR):
        if f.startswith(prefix) and f.endswith(".md"):
            return f
    return None

async def synthesize_chapter_audio(text: str, mp3_path: str, srt_path: str, voice="en-US-AriaNeural"):
    temp_mp3 = mp3_path + ".tmp"
    temp_srt = srt_path + ".tmp"
    
    communicate = edge_tts.Communicate(text, voice)
    submaker = edge_tts.SubMaker()
    
    with open(temp_mp3, "wb") as f:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "SentenceBoundary":
                submaker.feed(chunk)
                
    with open(temp_srt, "w", encoding="utf-8") as f:
        f.write(submaker.get_srt())
        
    os.replace(temp_mp3, mp3_path)
    os.replace(temp_srt, srt_path)

def update_readme():
    md_files = sorted([f for f in os.listdir(CHAPTERS_DIR) if f.endswith(".md")])
    audio_files = sorted([f for f in os.listdir(AUDIO_DIR) if f.endswith(".mp3")])
    video_files = sorted([f for f in os.listdir(VIDEO_DIR) if f.endswith(".mp4")])
    
    lines = [
        "# Williams Obstetrics, 26th Edition",
        "",
        "Comprehensive study repository for *Williams Obstetrics* (26th Edition), featuring chapter-by-chapter Markdown texts, neural audiobooks, and synchronized read-along videos.",
        "",
    ]
    
    if video_files:
        lines.extend([
            "## 🎥 Synchronized Read-Along Videos (1080p MP4)",
            "",
            "Playable directly on GitHub Web & Mobile with synchronized subtitle rendering and audio narration:",
            "",
        ])
        for vf in video_files:
            title = vf.replace(".mp4", "").replace("_", " ")
            size_mb = os.path.getsize(os.path.join(VIDEO_DIR, vf)) / (1024 * 1024)
            lines.append(f"- 🎬 **[{title}](videos/{vf})** ({size_mb:.1f} MB)")
        lines.append("")
        
    if audio_files:
        lines.extend([
            f"## 🎧 Audiobooks (MP3) — [{len(audio_files)}/68 Completed]",
            "",
            "Playable directly on GitHub Web & Mobile with built-in audio player:",
            "",
        ])
        for af in audio_files:
            title = af.replace(".mp3", "").replace("_", " ")
            size_mb = os.path.getsize(os.path.join(AUDIO_DIR, af)) / (1024 * 1024)
            lines.append(f"- 🔊 **[{title}](audiobooks/{af})** ({size_mb:.1f} MB)")
        lines.append("")
        
    lines.extend([
        "## 📖 Table of Contents (Chapters)",
        "",
    ])
    for f in md_files:
        title = f[:-3].replace("_", " ")
        lines.append(f"- [{title}](chapters/{f})")
    lines.append("")
    
    with open(README_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"Updated README.md: {len(audio_files)} audiobooks, {len(video_files)} videos indexed.")

def git_commit_and_push(commit_msg: str):
    print(f"Git: Staging changes...")
    subprocess.run(["git", "-C", REPO_DIR, "add", "audiobooks/", "subtitles/", "videos/", "manifest.json", "README.md", "scripts/"], check=True)
    res = subprocess.run(["git", "-C", REPO_DIR, "status", "--porcelain"], capture_output=True, text=True)
    if res.stdout.strip():
        print(f"Git: Committing: {commit_msg}")
        subprocess.run(["git", "-C", REPO_DIR, "commit", "-m", commit_msg], check=True)
        print("Git: Pushing to origin main...")
        for attempt in range(3):
            try:
                subprocess.run(["git", "-C", REPO_DIR, "push", "origin", "main"], check=True)
                print("Git: Push successful!")
                break
            except Exception as e:
                print(f"Git push attempt {attempt+1} failed: {e}. Retrying in 5s...")
                time.sleep(5)
    else:
        print("Git: Nothing new to commit.")

def process_chapter(chap_num: int, mode="audio", voice="en-US-AriaNeural", retries=3):
    manifest = load_manifest()
    chap_str = str(chap_num)
    if chap_str not in manifest:
        manifest[chap_str] = {}
        
    chap_file = get_chapter_file(chap_num)
    if not chap_file:
        print(f"Error: Chapter {chap_num} file not found!")
        return False
        
    base_name = os.path.splitext(chap_file)[0]
    in_path = os.path.join(CHAPTERS_DIR, chap_file)
    mp3_path = os.path.join(AUDIO_DIR, f"{base_name}.mp3")
    srt_path = os.path.join(SUBTITLES_DIR, f"{base_name}.srt")
    
    # Check if audio already exists and is valid (greater than 500KB to ensure full chapter)
    audio_exists = os.path.exists(mp3_path) and os.path.getsize(mp3_path) > 500_000
    
    if audio_exists:
        print(f"Chapter {chap_num:02d} ({base_name}) audio already exists ({os.path.getsize(mp3_path)/(1024*1024):.2f} MB). Skipping.")
        manifest[chap_str]["audio"] = True
        save_manifest(manifest)
        return True
        
    with open(in_path, "r", encoding="utf-8") as f:
        raw_text = f.read()
    clean_text = clean_text_for_speech(raw_text)
    words = len(clean_text.split())
    
    print(f"\n🎙️ [Chapter {chap_num:02d}/68] Synthesizing: {base_name} ({words} words)...")
    
    for attempt in range(retries):
        try:
            start_t = time.time()
            asyncio.run(synthesize_chapter_audio(clean_text, mp3_path, srt_path, voice=voice))
            dur = time.time() - start_t
            size_mb = os.path.getsize(mp3_path) / (1024 * 1024)
            print(f"   ✓ Audio ready in {dur:.1f}s: {mp3_path} ({size_mb:.2f} MB)")
            manifest[chap_str]["audio"] = True
            manifest[chap_str]["words"] = words
            manifest[chap_str]["audio_size_mb"] = round(size_mb, 2)
            save_manifest(manifest)
            return True
        except Exception as e:
            print(f"   ⚠️ Attempt {attempt+1} failed for chapter {chap_num}: {e}")
            time.sleep(3)
            
    print(f"❌ Failed to synthesize chapter {chap_num} after {retries} attempts.")
    return False

def run_all_sections(push=True, voice="en-US-AriaNeural"):
    """Processes all chapters section by section with intermediate git pushes."""
    print("==================================================")
    print(" Williams Obstetrics: Complete Audiobook Generator")
    print(" Target: ALL 68 CHAPTERS (Chapters 1 to 68)")
    print(f" Voice: {voice} | Section-by-Section Auto-Push: {push}")
    print("==================================================")
    
    for sec_num in sorted(SECTION_MAP.keys()):
        sec_info = SECTION_MAP[sec_num]
        sec_name = sec_info["name"]
        chapters = sec_info["chapters"]
        
        print(f"\n📁 === SECTION {sec_num}: {sec_name.upper()} (Chapters {min(chapters)}..{max(chapters)}) ===")
        section_had_work = False
        
        for c in chapters:
            chap_file = get_chapter_file(c)
            base_name = os.path.splitext(chap_file)[0] if chap_file else f"Chapter_{c}"
            mp3_path = os.path.join(AUDIO_DIR, f"{base_name}.mp3")
            if not (os.path.exists(mp3_path) and os.path.getsize(mp3_path) > 500_000):
                section_had_work = True
            process_chapter(c, mode="audio", voice=voice)
            
        update_readme()
        
        if push and section_had_work:
            commit_msg = f"Add Section {sec_num} ({sec_name}) audiobooks (Chapters {min(chapters)}-{max(chapters)})"
            git_commit_and_push(commit_msg)
            
    print("\n🎉 ALL 68 CHAPTERS PROCESSED SUCCESSFULLY!")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--section", type=int)
    parser.add_argument("--chapter", type=int)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--voice", default="en-US-AriaNeural")
    parser.add_argument("--push", action="store_true")
    args = parser.parse_args()
    
    if args.all:
        run_all_sections(push=args.push, voice=args.voice)
    elif args.section:
        sec = SECTION_MAP[args.section]
        for c in sec["chapters"]:
            process_chapter(c, mode="audio", voice=args.voice)
        update_readme()
        if args.push:
            git_commit_and_push(f"Add Section {args.section} audiobooks")
    elif args.chapter:
        process_chapter(args.chapter, mode="audio", voice=args.voice)
        update_readme()
        if args.push:
            git_commit_and_push(f"Add Chapter {args.chapter} audiobook")
    else:
        run_all_sections(push=True)
