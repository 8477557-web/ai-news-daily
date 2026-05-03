"""每日 AI 新闻抓取 — 多源聚合 + QQ 邮箱发送

支持三种类型的新闻源，可自由启用/禁用：
  1. API 类（需要 key）：GNews
  2. 免费 API 类（无需 key）：HackerNews
  3. RSS 类（无需 key，国内+国外）：36氪、机器之心、IT之家、TechCrunch

添加新源：写一个返回 list[dict] 的函数，注册到 SOURCES 列表即可。
"""
import os
import sys
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone, timedelta
from typing import Callable

import requests
import xml.etree.ElementTree as ET

# ---------- 邮箱配置 ----------
QQ_EMAIL = os.environ["QQ_EMAIL"]
QQ_SMTP_CODE = os.environ["QQ_SMTP_CODE"]
SMTP_SERVER = "smtp.qq.com"
SMTP_PORT = 465
BEIJING_TZ = timezone(timedelta(hours=8))

# ---------- 新闻源配置 ----------
# 通过环境变量控制是否启用某个源（"1"=启用，其他=禁用）
# 默认：GNews + HackerNews + 36氪 启用，其余按需开启
SOURCE_SWITCH = {
    "gnews":        os.getenv("SOURCE_GNEWS", "1"),
    "hackernews":   os.getenv("SOURCE_HACKERNEWS", "1"),
    "36kr_ai":      os.getenv("SOURCE_36KR", "1"),
    "jiqizhixin":    os.getenv("SOURCE_JIQIZHIXIN", "1"),   # 机器之心
    "ithome":        os.getenv("SOURCE_ITHOME", "1"),       # IT之家
    "techcrunch_ai": os.getenv("SOURCE_TECHCRUNCH", "1"),  # TechCrunch AI
}


# ============================================================
#  通用工具
# ============================================================

def _parse_feed(xml_text: str, item_tag: str = "item") -> list[dict]:
    """解析 RSS 2.0 / Atom feed，兼容命名空间，返回标准化文章列表"""
    # 去除 XML 声明中的编码声明，避免解析问题
    root = ET.fromstring(xml_text)

    # 判断是 Atom 还是 RSS
    tag = root.tag.lower()
    is_atom = "feed" in tag or "atom" in tag

    results = []

    if is_atom:
        # Atom 格式: <feed><entry>...</entry></feed>
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        for entry in root.findall("atom:entry", ns) or root.findall("entry"):
            title = _find_text(entry, "title")
            url = ""
            link_el = entry.find("atom:link", ns) if "atom" in str(root.tag) else entry.find("link")
            if link_el is not None:
                url = link_el.get("href", "")
            desc = _find_text(entry, "summary") or _find_text(entry, "content")
            pub = _find_text(entry, "published") or _find_text(entry, "updated")
            results.append({
                "title": title or "",
                "description": (desc or "")[:200],
                "url": url or "",
                "published": pub or "",
            })
    else:
        # RSS 2.0 格式: <rss><channel><item>...</item></channel></rss>
        for item in root.iter("item"):
            title = _find_text(item, "title")
            url = _find_text(item, "link")
            desc = _find_text(item, "description")
            pub = _find_text(item, "pubDate")
            results.append({
                "title": title or "",
                "description": (desc or "")[:200],
                "url": url or "",
                "published": pub or "",
            })

    return results


def _find_text(element: ET.Element, tag: str) -> str:
    """按本地名查找子元素文本，忽略命名空间"""
    for child in element:
        # 去掉命名空间前缀，如 {http://...}title → title
        local = child.tag.split("}", 1)[-1] if "}" in child.tag else child.tag
        if local == tag:
            return (child.text or "").strip()
    return ""


# ============================================================
#  新闻源实现 — 每个函数返回 list[dict]
#  dict 格式: {"title", "description", "url", "source", "published"}
# ============================================================

def fetch_gnews() -> list[dict]:
    """GNews API — 国际 AI 新闻（需要 GNEWS_API_KEY）"""
    api_key = os.getenv("GNEWS_API_KEY", "")
    if not api_key:
        print("[SKIP] GNews: 未设置 GNEWS_API_KEY")
        return []

    terms = ["artificial intelligence", "large language model", "AI agent", "generative AI"]
    results = []
    seen = set()

    for term in terms:
        try:
            resp = requests.get(
                "https://gnews.io/api/v4/search",
                params={"q": term, "lang": "en", "max": 10, "apikey": api_key},
                timeout=15,
            )
            resp.raise_for_status()
            for a in resp.json().get("articles", []):
                url = a.get("url", "")
                if url and url not in seen:
                    seen.add(url)
                    results.append({
                        "title": a.get("title", "无标题"),
                        "description": a.get("description", "") or "",
                        "url": url,
                        "source": f"GNews · {a.get('source', {}).get('name', '')}",
                        "published": a.get("publishedAt", ""),
                    })
        except Exception as e:
            print(f"[WARN] GNews 搜索 '{term}' 失败: {e}", file=sys.stderr)

    return results


def fetch_hackernews() -> list[dict]:
    """Hacker News — 搜索 AI 相关热门帖子（免费，无需 API Key）"""
    terms = ["artificial intelligence", "LLM", "AI agent", "ChatGPT"]
    results = []
    seen = set()

    for term in terms:
        try:
            resp = requests.get(
                "https://hn.algolia.com/api/v1/search_by_date",
                params={"query": term, "tags": "story", "hitsPerPage": 10},
                timeout=15,
            )
            resp.raise_for_status()
            for hit in resp.json().get("hits", []):
                url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit['objectID']}"
                if url not in seen:
                    seen.add(url)
                    results.append({
                        "title": hit.get("title", "无标题"),
                        "description": f"{hit.get('points', 0)} 分 · {hit.get('num_comments', 0)} 评论",
                        "url": url,
                        "source": "Hacker News",
                        "published": hit.get("created_at", ""),
                    })
        except Exception as e:
            print(f"[WARN] HackerNews 搜索 '{term}' 失败: {e}", file=sys.stderr)

    return results


def fetch_36kr_ai() -> list[dict]:
    """36氪 RSS — 国内科技新闻，按 AI 关键词过滤（免费）"""
    try:
        resp = requests.get(
            "https://36kr.com/feed",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        resp.raise_for_status()

        ai_kw = ["AI", "人工智能", "大模型", "智能体", "ChatGPT", "GPT", "LLM",
                 "机器学习", "深度学习", "Agent", "OpenAI", "Claude", "Gemini"]

        results = []
        for a in _parse_feed(resp.text):
            if any(kw.lower() in a["title"].lower() for kw in ai_kw):
                a["source"] = "36氪"
                results.append(a)
        return results[:15]
    except Exception as e:
        print(f"[WARN] 36氪 RSS 获取失败: {e}", file=sys.stderr)
        return []


def fetch_jiqizhixin() -> list[dict]:
    """机器之心 RSS — AI 专业媒体（免费）"""
    try:
        resp = requests.get(
            "https://www.jiqizhixin.com/rss",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        resp.raise_for_status()
        results = []
        for a in _parse_feed(resp.text):
            a["source"] = "机器之心"
            results.append(a)
        return results[:15]
    except Exception as e:
        print(f"[WARN] 机器之心 RSS 获取失败: {e}", file=sys.stderr)
        return []


def fetch_ithome() -> list[dict]:
    """IT之家 RSS — 综合科技新闻，按 AI 关键词过滤（免费）"""
    try:
        resp = requests.get(
            "https://www.ithome.com/rss/",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        resp.raise_for_status()

        ai_kw = ["AI", "人工智能", "大模型", "智能体", "ChatGPT", "GPT", "LLM",
                 "机器学习", "深度学习", "Agent", "OpenAI", "Claude", "Gemini",
                 "机器人", "自动驾驶", "芯片"]

        results = []
        for a in _parse_feed(resp.text):
            if any(kw.lower() in a["title"].lower() for kw in ai_kw):
                a["source"] = "IT之家"
                results.append(a)
        return results[:15]
    except Exception as e:
        print(f"[WARN] IT之家 RSS 获取失败: {e}", file=sys.stderr)
        return []


def fetch_techcrunch_ai() -> list[dict]:
    """TechCrunch AI 标签 RSS — 国际 AI 新闻（免费）"""
    try:
        resp = requests.get(
            "https://techcrunch.com/tag/artificial-intelligence/feed/",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        resp.raise_for_status()
        results = []
        for a in _parse_feed(resp.text):
            a["source"] = "TechCrunch"
            results.append(a)
        return results[:15]
    except Exception as e:
        print(f"[WARN] TechCrunch RSS 获取失败: {e}", file=sys.stderr)
        return []


# ============================================================
#  源注册表 — 添加新源在这里加一行就行
#  (标识, 函数, 来源地区)
# ============================================================
SOURCES: list[tuple[str, Callable[[], list[dict]], str]] = [
    ("gnews",         fetch_gnews,         "🌍 国际"),
    ("hackernews",    fetch_hackernews,    "🌍 国际"),
    ("36kr_ai",       fetch_36kr_ai,       "🇨🇳 国内"),
    ("jiqizhixin",    fetch_jiqizhixin,    "🇨🇳 国内"),
    ("ithome",        fetch_ithome,        "🇨🇳 国内"),
    ("techcrunch_ai", fetch_techcrunch_ai, "🌍 国际"),
]


# ============================================================
#  聚合、格式化、发送
# ============================================================

def collect_articles() -> list[dict]:
    """遍历所有启用的源，收集并去重"""
    all_articles: dict[str, dict] = {}  # url -> article

    for src_id, fetch_fn, region in SOURCES:
        if SOURCE_SWITCH.get(src_id, "0") != "1":
            print(f"[SKIP] {src_id} — 已禁用（设置 SOURCE_{src_id.upper()}=1 启用）")
            continue

        print(f"[FETCH] {region} {src_id}...")
        try:
            articles = fetch_fn()
            new_count = 0
            for a in articles:
                url = a.get("url", "")
                if url and url not in all_articles:
                    all_articles[url] = a
                    new_count += 1
            print(f"       获取 {len(articles)} 条，新增 {new_count} 条")
        except Exception as e:
            print(f"[ERROR] {src_id}: {e}", file=sys.stderr)

    result = list(all_articles.values())
    result.sort(key=lambda a: a.get("published", ""), reverse=True)
    return result[:40]  # 最多 40 条


def build_html(articles: list[dict]) -> str:
    """生成 HTML 邮件"""
    now_str = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M")
    rows = ""
    for a in articles:
        pub = a["published"].replace("T", " ").replace("Z", "")[:19] if a["published"] else ""
        desc = a["description"][:200] if a["description"] else ""
        rows += f"""
        <tr>
            <td style="padding:12px;border-bottom:1px solid #eee">
                <a href="{a['url']}" style="color:#1a73e8;text-decoration:none;font-weight:bold">{a['title']}</a>
                <div style="color:#555;font-size:14px;margin-top:4px">{desc}</div>
                <div style="color:#999;font-size:12px;margin-top:4px">
                    {pub} · <strong>{a['source']}</strong>
                </div>
            </td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:Arial,sans-serif;max-width:700px;margin:0 auto;background:#f5f5f5">
    <div style="background:#1a73e8;color:#fff;padding:20px;text-align:center">
        <h1 style="margin:0">🤖 每日 AI 新闻速递</h1>
        <p style="margin:5px 0 0;font-size:14px;opacity:0.85">{now_str} 更新 · 共 {len(articles)} 条 · 多源聚合</p>
    </div>
    <div style="background:#fff;padding:10px 0">
        <table style="width:100%;border-collapse:collapse">
            {rows}
        </table>
    </div>
    <div style="text-align:center;padding:15px;color:#999;font-size:12px">
        由 GitHub Actions 自动发送 · GNews + HackerNews + 36氪 等来源聚合
    </div>
</body>
</html>"""


def send_email(html: str, article_count: int):
    """通过 QQ 邮箱 SMTP 发送"""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"每日 AI 新闻速递 · {datetime.now(BEIJING_TZ).strftime('%Y-%m-%d')}（{article_count}条）"
    msg["From"] = QQ_EMAIL
    msg["To"] = QQ_EMAIL
    msg.attach(MIMEText(html, "html", "utf-8"))

    with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, timeout=15) as server:
        server.login(QQ_EMAIL, QQ_SMTP_CODE)
        server.sendmail(QQ_EMAIL, [QQ_EMAIL], msg.as_string())

    print(f"[OK] 邮件发送成功，共 {article_count} 条新闻 → {QQ_EMAIL}")


def main():
    enabled_count = sum(1 for v in SOURCE_SWITCH.values() if v == "1")
    print(f"[INFO] 已启用 {enabled_count}/{len(SOURCE_SWITCH)} 个新闻源，开始抓取...\n")

    articles = collect_articles()
    print(f"\n[INFO] 去重后共 {len(articles)} 条新闻")

    if not articles:
        print("[WARN] 未获取到任何新闻，跳过发送", file=sys.stderr)
        return

    html = build_html(articles)
    send_email(html, len(articles))


if __name__ == "__main__":
    main()
