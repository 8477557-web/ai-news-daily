"""将 digests/*.md 转换为 HTML 详情页"""

import re
from pathlib import Path

import markdown

DIGESTS_DIR = Path(__file__).parent.parent.parent / "digests"

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>AI News Daily | {date}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
               background: #f5f5f5; color: #333; line-height: 1.8; }}
        .container {{ max-width: 800px; margin: 0 auto; padding: 20px; }}
        .header {{ background: linear-gradient(135deg, #1a73e8, #0d47a1);
                   color: #fff; padding: 30px; border-radius: 12px; margin-bottom: 20px; }}
        .header h1 {{ font-size: 24px; margin-bottom: 8px; }}
        .header p {{ opacity: 0.85; font-size: 14px; }}
        .content {{ background: #fff; padding: 24px; border-radius: 12px; }}
        .content h1 {{ display: none; }}
        .content h2 {{ color: #1a73e8; font-size: 18px; margin: 24px 0 12px;
                       padding-bottom: 8px; border-bottom: 2px solid #e8f0fe; }}
        .content ul {{ margin-left: 20px; }}
        .content li {{ margin: 8px 0; }}
        .content a {{ color: #1a73e8; text-decoration: none; }}
        .content a:hover {{ text-decoration: underline; }}
        .footer {{ text-align: center; padding: 20px; color: #999; font-size: 12px; }}
        .nav {{ margin-bottom: 16px; }}
        .nav a {{ color: #1a73e8; text-decoration: none; font-size: 14px; }}
        .nav a:hover {{ text-decoration: underline; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="nav"><a href="index.html">← 返回列表</a></div>
        <div class="header">
            <h1>AI News Daily</h1>
            <p>{date} · 自动生成</p>
        </div>
        <div class="content">{html_content}</div>
        <div class="footer">
            由 GitHub Actions 每日自动生成 · AI 驱动摘要
        </div>
    </div>
</body>
</html>"""


def convert_md_to_html(md_path: Path) -> None:
    """将单个 Markdown 文件转换为 HTML"""
    date_match = re.search(r"(\d{4}-\d{2}-\d{2})", md_path.name)
    if not date_match:
        return
    date_str = date_match.group(1)

    md_text = md_path.read_text(encoding="utf-8")
    html_content = markdown.markdown(md_text, extensions=["extra", "nl2br"])

    html_path = md_path.parent / f"{date_str}.html"
    html_path.write_text(
        HTML_TEMPLATE.format(date=date_str, html_content=html_content),
        encoding="utf-8",
    )
    print(f"[OK] {html_path.name}")


def main():
    if not DIGESTS_DIR.exists():
        print("[WARN] digests/ 目录不存在")
        return

    md_files = sorted(DIGESTS_DIR.glob("????-??-??.md"), reverse=True)
    if not md_files:
        print("[WARN] 无 Markdown 文件")
        return

    print(f"[INFO] 找到 {len(md_files)} 个 Markdown 文件")
    for md_path in md_files:
        convert_md_to_html(md_path)

    print(f"[OK] HTML 详情页生成完成")


if __name__ == "__main__":
    main()
