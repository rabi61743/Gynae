#!/usr/bin/env python3
"""
Synchronized Audio-Video Generator for Williams Obstetrics chapters.
Combines edge-tts neural voice synthesis, exact sentence-level subtitle alignment (.srt),
and an aesthetic 1080p dark-slate reading card into an MP4 video playable on GitHub and mobile.
"""

import os
import re
import sys
import asyncio
import subprocess
import edge_tts
from PIL import Image, ImageDraw, ImageFont
import imageio_ffmpeg

FFMPEG_BIN = imageio_ffmpeg.get_ffmpeg_exe()

def clean_for_speech(md_text: str) -> str:
    """Prepares textbook markdown for smooth narration, removing references and citations."""
    # 1. Discard bibliographic references list at the end
    text = re.split(r"\n##\s*References\b", md_text, flags=re.IGNORECASE)[0]
    
    # 2. Convert Chapter title to spoken intro
    text = re.sub(r"^#\s*Chapter\s+(\d+):\s*(.*)$", r"Williams Obstetrics. Chapter \1: \2.", text, flags=re.MULTILINE)
    
    # 3. Section headers
    text = re.sub(r"^##\s*(.*)$", r"\1.", text, flags=re.MULTILINE)
    text = re.sub(r"^###\s*(.*)$", r"\1.", text, flags=re.MULTILINE)
    
    # 4. Handle Figures & Tables
    text = re.sub(r">\s*\*\*Figure\s+(\d+[-–]\d+):\*\*\s*(.*)", r"Figure \1. \2", text)
    text = re.sub(r">\s*\*\*Table\s+(\d+[-–]\d+):\*\*\s*(.*)", r"Table \1. \2", text)
    text = re.sub(r"^>\s*", "", text, flags=re.MULTILINE)
    
    # 5. Remove bold and markdown formatting
    text = text.replace("**", "").replace("*", "")
    
    # 6. Remove parenthetical citations
    text = re.sub(r"\([A-Z][A-Za-z\s–,-]+,?\s*\d{4}[a-z]?\)", "", text)
    text = re.sub(r"\(Chap\.\s*\d+,\s*p\.\s*\d+\)", "", text)
    text = re.sub(r"\(see\s+Table\s+[\d-]+\)", "", text)
    text = re.sub(r"\(Fig\.\s*[\d-]+[a-z]?\)", "", text)
    text = re.sub(r"\(Table\s*[\d-]+[a-z]?\)", "", text)
    
    # 7. Clean bullet dashes and normalize spaces
    text = re.sub(r"^-\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s+", " ", text).strip()
    return text

def create_background_card(title: str, output_path: str, width=1920, height=1080):
    """Draws an aesthetic 1080p reading canvas with chapter header."""
    img = Image.new("RGB", (width, height), color=(15, 23, 42))  # slate-900
    draw = ImageDraw.Draw(img)
    
    # Cyan top accent strip
    draw.rectangle([(0, 0), (width, 8)], fill=(56, 189, 248))
    
    # Text container card
    card_x0, card_y0, card_x1, card_y1 = 160, 180, 1760, 900
    draw.rectangle([(card_x0, card_y0), (card_x1, card_y1)], fill=(30, 41, 59), outline=(51, 65, 85), width=2)
    
    img.save(output_path)

async def generate_audio_and_subtitles(text: str, mp3_path: str, srt_path: str, voice="en-US-AriaNeural"):
    """Synthesizes speech and extracts exact sentence boundary timestamps."""
    communicate = edge_tts.Communicate(text, voice)
    submaker = edge_tts.SubMaker()
    
    with open(mp3_path, "wb") as f:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "SentenceBoundary":
                submaker.feed(chunk)
                
    with open(srt_path, "w", encoding="utf-8") as f:
        f.write(submaker.get_srt())

def render_chapter_video(chapter_md_path: str, output_mp4_path: str, voice="en-US-AriaNeural"):
    """Full pipeline: Markdown -> MP3 + SRT -> 1080p MP4."""
    out_dir = os.path.dirname(output_mp4_path)
    os.makedirs(out_dir, exist_ok=True)
    temp_prefix = os.path.join(out_dir, "temp_" + os.path.splitext(os.path.basename(output_mp4_path))[0])
    
    mp3_tmp = f"{temp_prefix}.mp3"
    srt_tmp = f"{temp_prefix}.srt"
    bg_tmp = f"{temp_prefix}_bg.png"
    
    print(f"📖 Step 1: Preprocessing {os.path.basename(chapter_md_path)}...")
    with open(chapter_md_path, "r", encoding="utf-8") as f:
        raw_text = f.read()
    clean_text = clean_for_speech(raw_text)
    print(f"   Cleaned text: {len(clean_text.split())} words.")
    
    print(f"🎙️ Step 2: Synthesizing speech and generating sentence timestamps...")
    asyncio.run(generate_audio_and_subtitles(clean_text, mp3_tmp, srt_tmp, voice=voice))
    print(f"   Generated audio: {os.path.getsize(mp3_tmp) / (1024*1024):.2f} MB")
    
    print(f"🎨 Step 3: Generating 1080p reading canvas...")
    create_background_card("Williams Obstetrics, 26e", bg_tmp)
    
    print(f"🎬 Step 4: Encoding synchronized MP4 with FFmpeg...")
    # Optimized for fast encoding, low size (<40MB), and high audio quality
    cmd = [
        FFMPEG_BIN, "-y",
        "-loop", "1", "-i", bg_tmp,
        "-i", mp3_tmp,
        "-vf", (
            f"subtitles={srt_tmp}:force_style="
            "'FontName=Helvetica,FontSize=32,PrimaryColour=&H00FFFFFF&,"
            "OutlineColour=&H00000000&,Outline=1,BackColour=&H80000000&,"
            "BorderStyle=3,MarginV=260,Alignment=10'"
        ),
        "-c:v", "libx264", "-tune", "stillimage", "-crf", "28", "-preset", "veryfast",
        "-c:a", "aac", "-b:a", "128k",
        "-pix_fmt", "yuv420p", "-shortest",
        output_mp4_path
    ]
    subprocess.run(cmd, check=True)
    
    # Cleanup temp assets
    for tmp in [mp3_tmp, srt_tmp, bg_tmp]:
        if os.path.exists(tmp):
            os.remove(tmp)
            
    size_mb = os.path.getsize(output_mp4_path) / (1024 * 1024)
    print(f"✅ Finished! Video saved to: {output_mp4_path} ({size_mb:.2f} MB)")

if __name__ == "__main__":
    if len(sys.argv) > 2:
        in_file = sys.argv[1]
        out_file = sys.argv[2]
    else:
        in_file = "/Volumes/Kingston/Gynae/chapters/Chapter_01_Overview_of_Obstetrics.md"
        out_file = "/Volumes/Kingston/Gynae/videos/Chapter_01_Overview_of_Obstetrics.mp4"
    render_chapter_video(in_file, out_file)
