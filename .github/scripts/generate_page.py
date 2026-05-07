"""生成首页列表 + 分页（index.html, page-2.html, ...）"""

import re
from pathlib import Path
from datetime import datetime

DIGESTS_DIR = Path(__file__).parent.parent.parent / "digests"
ROOT_DIR = Path(__file__).parent.parent.parent
ITEMS_PER_PAGE = 10

INDEX_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>AI News Daily</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
               background: #f5f5f5; color: #333; }}
        .container {{ max-width: 800px; margin: 0 auto; padding: 20px; }}
        .header {{ background: linear-gradient(135deg, #1a73e8, #0d47a1);
                   color: #fff; padding: 30px; border-radius: 12px; margin-bottom: 20px;
                   text-align: center; }}
        .header h1 {{ font-size: 28px; margin-bottom: 8px; }}
        .header p {{ opacity: 0.85; font-size: 14px; }}
        .list {{ background: #fff; border-radius: 12px; overflow: hidden; }}
        .item {{ padding: 16px 20px; border-bottom: 1px solid #f0f0f0; transition: background 0.2s; }}
        .item:hover {{ background: #f8f9fa; }}
        .item a {{ color: #333; text-decoration: none; font-size: 16px; font-weight: 500; }}
        .item a:hover {{ color: #1a73e8; }}
        .item .date {{ color: #999; font-size: 13px; margin-top: 4px; }}
        .pagination {{ display: flex; justify-content: center; gap: 8px; margin-top: 20px; }}
        .pagination a {{ padding: 8px 16px; background: #fff; border-radius: 8px;
                         color: #1a73e8; text-decoration: none; font-size: 14px; }}
        .pagination a:hover {{ background: #e8f0fe; }}
        .pagination .current {{ background: #1a73e8; color: #fff; }}
        .footer {{ text-align: center; padding: 20px; color: #999; font-size: 12px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>AI News Daily</h1>
            <p>每日 AI 科技新闻摘要 · 自动聚合 · AI 驱动</p>
        </div>
        <div class="list">
{items}
        </div>
        <div class="pagination">
{pagination}
        </div>
        <div class="footer">
            由 GitHub Actions 每日自动生成 · {total} 期简报
        </div>
    </div>
</body>
</html>"""


def get_digest_dates() -> list[str]:
    """获取所有有 Markdown 文件的日期，按倒序排列"""
    if not DIGESTS_DIR.exists():
        return []
    dates = []
    for p in DIGESTS_DIR.glob("????-??-??.md"):
        m = re.search(r"(\d{4}-\d{2}-\d{2})", p.name)
        if m and m.group(1) != "latest":
            dates.append(m.group(1))
    return sorted(dates, reverse=True)


def build_item_html(date_str: str) -> str:
    """生成单条列表项 HTML"""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        display = dt.strftime("%Y 年 %m 月 %d 日")
    except ValueError:
        display = date_str
    return f"""            <div class="item">
                <a href="digests/{date_str}.html">{display}</a>
                <div class="date">{date_str}</div>
            </div>"""


def build_pagination_html(current_page: int, total_pages: int) -> str:
    """生成分页导航 HTML"""
    parts = []
    for i in range(1, total_pages + 1):
        if i == 1:
            href = "index.html"
        else:
            href = f"page-{i}.html"
        cls = ' class="current"' if i == current_page else ""
        parts.append(f'            <a href="{href}"{cls}>{i}</a>')
    return "\n".join(parts)


def generate_page(dates: list[str], page_num: int) -> str:
    """生成某一页的 HTML"""
    start = (page_num - 1) * ITEMS_PER_PAGE
    end = start + ITEMS_PER_PAGE
    page_dates = dates[start:end]

    items_html = "\n".join(build_item_html(d) for d in page_dates)
    total_pages = max(1, (len(dates) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
    pagination_html = build_pagination_html(page_num, total_pages)

    return INDEX_TEMPLATE.format(
        items=items_html,
        pagination=pagination_html,
        total=len(dates),
    )


def main():
    dates = get_digest_dates()
    if not dates:
        print("[WARN] 无简报数据")
        return

    total_pages = max(1, (len(dates) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
    print(f"[INFO] {len(dates)} 期简报，{total_pages} 页")

    for page_num in range(1, total_pages + 1):
        html = generate_page(dates, page_num)
        if page_num == 1:
            out_path = ROOT_DIR / "index.html"
        else:
            out_path = ROOT_DIR / f"page-{page_num}.html"
        out_path.write_text(html, encoding="utf-8")
        print(f"[OK] {out_path.name}")

    # 清理多余的分页文件
    for old_page in ROOT_DIR.glob("page-*.html"):
        m = re.search(r"page-(\d+)\.html", old_page.name)
        if m and int(m.group(1)) > total_pages:
            old_page.unlink()
            print(f"[DEL] {old_page.name}")

    print(f"[OK] 首页分页生成完成")


if __name__ == "__main__":
    main()
