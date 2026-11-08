#!/usr/bin/env python3
"""
PKM 本地同步客户端
用于将服务器上的笔记同步到本地磁盘
"""

import os
import json
import requests
from datetime import datetime
from pathlib import Path
import argparse
import schedule
import time


class PKMSyncClient:
    def __init__(self, server_url: str, backup_dir: str):
        self.server_url = server_url.rstrip('/')
        self.backup_dir = Path(backup_dir)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
    
    def sync_notes(self):
        """同步所有笔记到本地"""
        print(f"[{datetime.now()}] Starting sync...")
        
        try:
            # 获取所有笔记
            response = requests.get(
                f"{self.server_url}/api/notes/",
                params={"limit": 1000}
            )
            response.raise_for_status()
            data = response.json()
            
            notes = data.get("items", [])
            total = data.get("total", 0)
            
            print(f"Found {total} notes on server")
            
            # 创建备份目录结构
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = self.backup_dir / timestamp
            backup_path.mkdir(exist_ok=True)
            
            # 保存笔记
            for note in notes:
                self._save_note(note, backup_path)
            
            # 保存元数据
            metadata = {
                "sync_time": datetime.now().isoformat(),
                "total_notes": total,
                "server_url": self.server_url
            }
            
            with open(backup_path / "metadata.json", "w", encoding="utf-8") as f:
                json.dump(metadata, f, ensure_ascii=False, indent=2)
            
            # 创建最新备份的符号链接
            latest_link = self.backup_dir / "latest"
            if latest_link.exists() or latest_link.is_symlink():
                latest_link.unlink()
            
            # Windows 上创建文件而非符号链接
            with open(self.backup_dir / "latest.txt", "w") as f:
                f.write(str(backup_path))
            
            print(f"Sync completed: {len(notes)} notes saved to {backup_path}")
            return True
            
        except Exception as e:
            print(f"Sync failed: {e}")
            return False
    
    def _save_note(self, note: dict, backup_path: Path):
        """保存单个笔记"""
        note_id = note.get("id")
        title = note.get("title", "Untitled")
        
        # 清理文件名
        safe_title = "".join(c for c in title if c.isalnum() or c in (' ', '-', '_'))
        safe_title = safe_title[:50]  # 限制长度
        
        filename = f"{note_id}_{safe_title}.md"
        filepath = backup_path / filename
        
        # 生成 Markdown 内容
        content = f"""---
id: {note.get('id')}
title: {note.get('title')}
source: {note.get('source')}
category: {note.get('category')}
tags: {', '.join(note.get('tags', []))}
created_at: {note.get('created_at')}
updated_at: {note.get('updated_at')}
---

# {note.get('title')}

{note.get('content', '')}

---

## AI 摘要
{note.get('ai_summary', '未生成')}
"""
        
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)


def main():
    parser = argparse.ArgumentParser(description="PKM 本地同步客户端")
    parser.add_argument(
        "--server",
        default="http://localhost:8080",
        help="PKM 服务器地址"
    )
    parser.add_argument(
        "--backup-dir",
        default="./pkm-backup",
        help="本地备份目录"
    )
    parser.add_argument(
        "--schedule",
        help="定时同步，格式: HH:MM (例如: 02:00)"
    )
    parser.add_argument(
        "--now",
        action="store_true",
        help="立即执行一次同步"
    )
    
    args = parser.parse_args()
    
    client = PKMSyncClient(args.server, args.backup_dir)
    
    # 立即执行
    if args.now or not args.schedule:
        client.sync_notes()
    
    # 定时执行
    if args.schedule:
        schedule.every().day.at(args.schedule).do(client.sync_notes)
        print(f"Scheduled daily sync at {args.schedule}")
        
        while True:
            schedule.run_pending()
            time.sleep(60)


if __name__ == "__main__":
    main()
