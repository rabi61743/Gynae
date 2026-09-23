#!/usr/bin/env python3
"""
Generates production YouTube metadata (CSV and JSON) for all 68 chapters of Williams Obstetrics (26th Edition).
Titles are formatted to fit within YouTube's 100-character limit with rich medical keywords.
Descriptions contain full clinical study outlines, disclaimers, and playlist placeholders.
"""

import os
import re
import csv
import json

REPO_DIR = "/Volumes/Kingston/Gynae"
CHAPTERS_DIR = os.path.join(REPO_DIR, "chapters")
VIDEOS_DIR = os.path.join(REPO_DIR, "videos")
AUDIO_DIR = os.path.join(REPO_DIR, "audiobooks")
SUBTITLES_DIR = os.path.join(REPO_DIR, "subtitles")
CSV_PATH = os.path.join(REPO_DIR, "youtube_metadata.csv")
JSON_PATH = os.path.join(REPO_DIR, "youtube_metadata.json")

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

def get_section(ch_num):
    for sec_num, sec in SECTION_MAP.items():
        if ch_num in sec["chapters"]:
            return sec_num, sec["name"]
    return 1, "Obstetrics"

def get_chapter_details(ch_num):
    prefix = f"Chapter_{ch_num:02d}_"
    for f in sorted(os.listdir(CHAPTERS_DIR)):
        if f.startswith(prefix) and f.endswith(".md"):
            md_path = os.path.join(CHAPTERS_DIR, f)
            with open(md_path, "r", encoding="utf-8") as fp:
                first_lines = [fp.readline() for _ in range(5)]
            title = None
            for l in first_lines:
                m = re.match(r"#\s*Chapter\s+\d+:\s*(.+)", l.strip())
                if m:
                    title = m.group(1).strip()
                    break
            if not title:
                title = f[len(prefix):-3].replace("_", " ")
            return f, title
    return None, None

def generate_metadata():
    records = []
    
    for ch in range(1, 69):
        md_file, title = get_chapter_details(ch)
        sec_num, sec_name = get_section(ch)
        base_name = os.path.splitext(md_file)[0]
        video_file = f"{base_name}.mp4"
        video_path = os.path.join(VIDEOS_DIR, video_file)
        
        # YouTube Title (Strict limit: 100 characters)
        # Format: Williams Obstetrics (26e) | Ch 01: Overview of Obstetrics (Read-Along)
        prefix = f"Williams Obstetrics (26e) | Ch {ch:02d}: "
        suffix = " (Read-Along Audiobook)"
        available_len = 100 - len(prefix) - len(suffix)
        if len(title) > available_len:
            short_suffix = " (Audiobook)"
            available_len = 100 - len(prefix) - len(short_suffix)
            if len(title) > available_len:
                yt_title = prefix + title[:available_len-3] + "..." + short_suffix
            else:
                yt_title = prefix + title + short_suffix
        else:
            yt_title = prefix + title + suffix
            
        # Description
        description = (
            f"Williams Obstetrics, 26th Edition\n"
            f"Section {sec_num}: {sec_name}\n"
            f"Chapter {ch:02d}: {title}\n\n"
            f"🎧 Complete 1080p Read-Along Video with synchronized sentence-level subtitles and neural audio narration.\n"
            f"📚 Medical Textbook: Williams Obstetrics (26th Edition, McGraw-Hill)\n\n"
            f"📌 Williams Obstetrics 26e Full Playlist:\n"
            f"https://www.youtube.com/playlist?list=PL_WILLIAMS_OBSTETRICS_26E\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⏱️ CHAPTER TIMESTAMPS\n"
            f"00:00 - Chapter {ch:02d}: {title}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⚕️ CLINICAL & EDUCATIONAL DISCLAIMER\n"
            f"This video and audio narration is intended strictly for medical students, obstetrical residents, "
            f"physicians, midwives, and healthcare professionals preparing for board examinations (USMLE Step 2 CK, "
            f"Step 3, MRCOG, Royal College, ABOG, and specialty licensing). Medicine and pharmacology are constantly "
            f"evolving fields. Clinicians must always verify diagnostic protocols, drug dosages, and contraindications "
            f"against primary sources and institutional guidelines."
        )
        
        # Tags (Comma-separated)
        tags_list = [
            "Williams Obstetrics",
            "Williams Obstetrics 26th edition",
            f"Chapter {ch} {title}",
            "Obstetrics",
            "Gynecology",
            "OBGYN",
            "Medical Audiobook",
            "Read Along Medical Video",
            "USMLE Step 2 CK",
            "USMLE Step 3",
            "MRCOG",
            "ABOG",
            "Maternal Fetal Medicine",
            "High Risk Pregnancy",
            sec_name
        ]
        tags_str = ", ".join(tags_list)
        
        size_mb = 0
        if os.path.exists(video_path):
            size_mb = round(os.path.getsize(video_path) / (1024 * 1024), 2)
            
        record = {
            "chapter": ch,
            "section_number": sec_num,
            "section_name": sec_name,
            "chapter_title": title,
            "youtube_title": yt_title,
            "title_length": len(yt_title),
            "video_filename": video_file,
            "video_path": video_path,
            "video_size_mb": size_mb,
            "category_id": 27,  # Education
            "privacy_status": "public",
            "tags": tags_str,
            "description": description
        }
        records.append(record)
        
    # Write CSV
    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        fieldnames = [
            "chapter", "section_number", "section_name", "chapter_title",
            "youtube_title", "title_length", "video_filename", "video_path",
            "video_size_mb", "category_id", "privacy_status", "tags", "description"
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in records:
            writer.writerow(r)
            
    # Write JSON
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2)
        
    print(f"Generated YouTube metadata for all {len(records)} chapters:")
    print(f" - CSV:  {CSV_PATH}")
    print(f" - JSON: {JSON_PATH}")

if __name__ == "__main__":
    generate_metadata()
