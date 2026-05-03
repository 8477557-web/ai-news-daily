"""每日 AI 新闻摘要 — 多源聚合 + 中文翻译 + 智能摘要 + QQ 邮箱发送

新闻源（可开关）：
  API 类：GNews、Bing Search
  免费 API：HackerNews
  RSS 类：36氪、机器之心、IT之家、TechCrunch
  网页抓取：百度新闻

输出：昨日 AI 热点摘要（≤600字）+ 来源链接
"""
import os
import re
import sys
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone, timedelta, date
from typing import Callable

import requests
import xml.etree.ElementTree as ET

from deep_translator import GoogleTranslator

# ---------- 邮箱配置 ----------
QQ_EMAIL = os.environ["QQ_EMAIL"]
QQ_SMTP_CODE = os.environ["QQ_SMTP_CODE"]
SMTP_SERVER = "smtp.qq.com"
SMTP_PORT = 465
BEIJING_TZ = timezone(timedelta(hours=8))

# ---------- 新闻源开关 ----------
SOURCE_SWITCH = {
    "gnews":        os.getenv("SOURCE_GNEWS", "1"),
    "bing":         os.getenv("SOURCE_BING", "1"),
    "baidu":        os.getenv("SOURCE_BAIDU", "1"),
    "hackernews":   os.getenv("SOURCE_HACKERNEWS", "1"),
    "36kr_ai":      os.getenv("SOURCE_36KR", "1"),
    "jiqizhixin":   os.getenv("SOURCE_JIQIZHIXIN", "1"),
    "ithome":       os.getenv("SOURCE_ITHOME", "1"),
    "techcrunch_ai": os.getenv("SOURCE_TECHCRUNCH", "1"),
}

MAX_DIGEST_CHARS = 600
MAX_SOURCES = 10

# ============================================================
#  通用工具：RSS/Atom 解析、日期处理
# ============================================================

def _parse_feed(xml_text: str) -> list[dict]:
    """解析 RSS 2.0 / Atom feed，兼容命名空间"""
    root = ET.fromstring(xml_text)
    tag = root.tag.lower()
    is_atom = "feed" in tag or "atom" in tag
    results = []

    if is_atom:
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        for entry in root.findall("atom:entry", ns) or root.findall("entry"):
            title = _find_text(entry, "title")
            url = ""
            link_el = entry.find("atom:link", ns) if "atom" in str(root.tag) else entry.find("link")
            if link_el is not None:
                url = link_el.get("href", "")
            desc = _find_text(entry, "summary") or _find_text(entry, "content")
            pub = _find_text(entry, "published") or _find_text(entry, "updated")
            results.append({"title": title or "", "description": (desc or "")[:200],
                            "url": url or "", "published": pub or ""})
    else:
        for item in root.iter("item"):
            results.append({
                "title": _find_text(item, "title") or "",
                "description": (_find_text(item, "description") or "")[:200],
                "url": _find_text(item, "link") or "",
                "published": _find_text(item, "pubDate") or "",
            })
    return results


def _find_text(element: ET.Element, tag: str) -> str:
    """按本地名查找子元素文本，忽略命名空间"""
    for child in element:
        local = child.tag.split("}", 1)[-1] if "}" in child.tag else child.tag
        if local == tag:
            return (child.text or "").strip()
    return ""


def _get_yesterday() -> date:
    """返回北京时间昨天"""
    return (datetime.now(BEIJING_TZ) - timedelta(days=1)).date()


def _parse_date(published_str: str) -> date | None:
    """尝试解析各种时间格式，返回北京时间日期"""
    if not published_str:
        return None
    s = published_str.strip()

    # ISO 8601: 2026-05-03T12:00:00Z
    for fmt in ["%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%d %H:%M:%S"]:
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(BEIJING_TZ).date()
        except ValueError:
            continue

    # RFC 2822: Mon, 03 May 2026 12:00:00 +0000 / GMT
    for fmt in ["%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z"]:
        try:
            return datetime.strptime(s, fmt).astimezone(BEIJING_TZ).date()
        except ValueError:
            continue

    # 百度: "3小时前" / "昨天" / "5月2日"
    now = datetime.now(BEIJING_TZ)
    if "小时前" in s:
        try:
            hours = int(re.search(r"(\d+)", s).group(1))
            return (now - timedelta(hours=hours)).date()
        except (ValueError, AttributeError):
            pass
    if "昨天" in s or "昨日" in s:
        return (now - timedelta(days=1)).date()
    if "分钟前" in s or "刚刚" in s:
        return now.date()
    m = re.match(r"(\d{1,2})月(\d{1,2})日", s)
    if m:
        return date(now.year, int(m.group(1)), int(m.group(2)))

    return None


def _is_yesterday(published_str: str) -> bool:
    d = _parse_date(published_str)
    return d == _get_yesterday() if d else False


# ============================================================
#  翻译
# ============================================================

_CJK_RE = re.compile(r"[一-鿿㐀-䶿豈-﫿]+")

_translator: GoogleTranslator | None = None


def _has_chinese(text: str) -> bool:
    return bool(_CJK_RE.search(text))


def translate_articles(articles: list[dict]) -> list[dict]:
    """英文标题/摘要翻译为中文"""
    global _translator
    if _translator is None:
        _translator = GoogleTranslator(source="en", target="zh-CN")

    texts: list[str] = []
    mappings: list[tuple[int, str]] = []  # (article_index, field)

    for i, a in enumerate(articles):
        for field in ["title", "description"]:
            text = a.get(field, "")
            if text and not _has_chinese(text):
                mappings.append((i, field))
                texts.append(text[:300])

    if not texts:
        print("[INFO] 无需翻译")
        return articles

    print(f"[INFO] 翻译 {len(texts)} 段文本...")
    for idx, text in enumerate(texts):
        try:
            result = _translator.translate(text)
            art_idx, field = mappings[idx]
            articles[art_idx][f"{field}_cn"] = result
        except Exception as e:
            print(f"[WARN] 翻译失败: {e}", file=sys.stderr)

    return articles


# ============================================================
#  摘要生成
# ============================================================

def generate_digest(articles: list[dict]) -> str:
    """用关键词加权算法从文章标题/摘要中提取 ≤600 字的中文摘要"""
    texts = []
    for a in articles:
        title = a.get("title_cn") or a.get("title", "")
        desc = a.get("desc_cn") or a.get("description", "")
        if title:
            texts.append(title)
        if desc:
            texts.append(desc)

    full_text = "。".join(texts)
    if not full_text.strip():
        return "暂无昨日 AI 新闻摘要。"

    # 按句号、感叹号、问号切分句子
    sentences = [s.strip() for s in re.split(r"[。！？\n]+", full_text) if len(s.strip()) >= 6]
    if not sentences:
        sentences = [full_text[:MAX_DIGEST_CHARS]]

    # 构建关键词词频表
    keywords = ["AI", "人工智能", "大模型", "智能体", "Agent", "OpenAI", "ChatGPT", "GPT",
                "LLM", "Claude", "Gemini", "机器学习", "深度学习", "生成式", "训练",
                "发布", "开源", "融资", "模型", "数据", "应用", "推理", "多模态",
                "人形机器人", "自动驾驶", "芯片", "算力"]
    word_score: dict[str, int] = {}
    for kw in keywords:
        word_score[kw] = full_text.lower().count(kw.lower())

    # 为每句打分：关键词出现次数 + 位置加分（标题靠前加分）
    scored = []
    for i, sent in enumerate(sentences):
        score = sum(word_score.get(kw, 0) for kw in keywords
                    if kw.lower() in sent.lower())
        score += max(0, (len(sentences) - i) / len(sentences))  # 靠前加权
        scored.append((score, sent))

    scored.sort(key=lambda x: x[0], reverse=True)

    # 按原文顺序取高分句直到接近 600 字
    top_sentences = scored[:8]
    top_sentences.sort(key=lambda x: sentences.index(x[1]))  # 恢复原文顺序

    digest = ""
    for _, sent in top_sentences:
        if len(digest) + len(sent) + 1 <= MAX_DIGEST_CHARS:
            digest += sent + "。"
        else:
            break

    digest = digest.strip("。")
    if len(digest) > MAX_DIGEST_CHARS:
        digest = digest[:MAX_DIGEST_CHARS - 3] + "..."

    return digest if digest else "暂无昨日 AI 新闻摘要。"


# ============================================================
#  新闻源实现
# ============================================================

def fetch_gnews() -> list[dict]:
    """GNews API — 国际 AI 新闻"""
    api_key = os.getenv("GNEWS_API_KEY", "")
    if not api_key:
        print("[SKIP] GNews: 未设置 GNEWS_API_KEY")
        return []

    terms = ["artificial intelligence", "large language model", "AI agent", "generative AI"]
    results, seen = [], set()
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
                        "title": a.get("title", ""),
                        "description": a.get("description", "") or "",
                        "url": url,
                        "source": f"GNews·{a.get('source', {}).get('name', '')}",
                        "published": a.get("publishedAt", ""),
                    })
        except Exception as e:
            print(f"[WARN] GNews '{term}': {e}", file=sys.stderr)
    return results


def fetch_bing() -> list[dict]:
    """Bing News Search API — AI 新闻"""
    api_key = os.getenv("BING_API_KEY", "")
    if not api_key:
        print("[SKIP] Bing: 未设置 BING_API_KEY（Azure 免费申请）")
        return []

    results = []
    for query in ["AI artificial intelligence", "大模型 LLM", "AI agent"]:
        try:
            resp = requests.get(
                "https://api.bing.microsoft.com/v7.0/news/search",
                params={"q": query, "count": 10, "freshness": "Day", "mkt": "zh-CN"},
                headers={"Ocp-Apim-Subscription-Key": api_key},
                timeout=15,
            )
            resp.raise_for_status()
            for a in resp.json().get("value", []):
                results.append({
                    "title": a.get("name", ""),
                    "description": a.get("description", "") or "",
                    "url": a.get("url", ""),
                    "source": f"Bing·{a.get('provider', [{}])[0].get('name', '')}",
                    "published": a.get("datePublished", ""),
                })
        except Exception as e:
            print(f"[WARN] Bing '{query}': {e}", file=sys.stderr)
    return results


def fetch_baidu() -> list[dict]:
    """百度新闻搜索 — AI 相关"""
    results = []
    queries = ["AI 人工智能", "大模型", "AI智能体", "OpenAI ChatGPT"]
    for query in queries:
        try:
            resp = requests.get(
                "https://news.baidu.com/ns",
                params={"word": query, "pn": 0, "tn": "news"},
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                                       "Chrome/120.0.0.0 Safari/537.36"},
                timeout=15,
            )
            if resp.status_code != 200:
                continue

            # 从 HTML 中提取标题/链接/摘要/时间
            titles = re.findall(r'<a[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>', resp.text)
            for url, title_raw in titles:
                title = re.sub(r"<[^>]+>", "", title_raw).strip()
                if not title or len(title) < 5:
                    continue
                # 提取摘要
                desc_match = re.search(rf"{re.escape(title[:10])}.*?<span[^>]*>(.*?)</span>", resp.text, re.DOTALL)
                desc = re.sub(r"<[^>]+>", "", desc_match.group(1)).strip() if desc_match else ""
                # 提取时间
                time_match = re.search(r"(\d+小时前|昨天|\d+分钟前|\d{1,2}月\d{1,2}日)", resp.text)
                pub = time_match.group(1) if time_match else ""

                results.append({
                    "title": title,
                    "description": desc[:200],
                    "url": url,
                    "source": "百度新闻",
                    "published": pub,
                })

            if len(results) >= 10:
                break
        except Exception as e:
            print(f"[WARN] 百度 '{query}': {e}", file=sys.stderr)

    # 去重
    seen = set()
    unique = []
    for r in results:
        if r["url"] not in seen:
            seen.add(r["url"])
            unique.append(r)
    return unique[:15]


def fetch_hackernews() -> list[dict]:
    """Hacker News — AI 相关热门帖子"""
    terms = ["artificial intelligence", "LLM", "AI agent", "ChatGPT"]
    results, seen = [], set()
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
                        "title": hit.get("title", ""),
                        "description": f"{hit.get('points', 0)} 分 · {hit.get('num_comments', 0)} 评论",
                        "url": url,
                        "source": "Hacker News",
                        "published": hit.get("created_at", ""),
                    })
        except Exception as e:
            print(f"[WARN] HN '{term}': {e}", file=sys.stderr)
    return results


def fetch_36kr_ai() -> list[dict]:
    """36氪 RSS — AI 关键词过滤"""
    try:
        resp = requests.get("https://36kr.com/feed",
                            headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
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
        print(f"[WARN] 36氪: {e}", file=sys.stderr)
        return []


def fetch_jiqizhixin() -> list[dict]:
    """机器之心 RSS"""
    try:
        resp = requests.get("https://www.jiqizhixin.com/rss",
                            headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        resp.raise_for_status()
        results = []
        for a in _parse_feed(resp.text):
            a["source"] = "机器之心"
            results.append(a)
        return results[:15]
    except Exception as e:
        print(f"[WARN] 机器之心: {e}", file=sys.stderr)
        return []


def fetch_ithome() -> list[dict]:
    """IT之家 RSS — AI 关键词过滤"""
    try:
        resp = requests.get("https://www.ithome.com/rss/",
                            headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
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
        print(f"[WARN] IT之家: {e}", file=sys.stderr)
        return []


def fetch_techcrunch_ai() -> list[dict]:
    """TechCrunch AI RSS"""
    try:
        resp = requests.get("https://techcrunch.com/tag/artificial-intelligence/feed/",
                            headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        resp.raise_for_status()
        results = []
        for a in _parse_feed(resp.text):
            a["source"] = "TechCrunch"
            results.append(a)
        return results[:15]
    except Exception as e:
        print(f"[WARN] TechCrunch: {e}", file=sys.stderr)
        return []


# ============================================================
#  源注册表
# ============================================================
SOURCES: list[tuple[str, Callable[[], list[dict]], str]] = [
    ("gnews",        fetch_gnews,         "🌍 GNews"),
    ("bing",         fetch_bing,          "🌍 Bing"),
    ("baidu",        fetch_baidu,         "🇨🇳 百度"),
    ("hackernews",   fetch_hackernews,    "🌍 HN"),
    ("36kr_ai",      fetch_36kr_ai,       "🇨🇳 36氪"),
    ("jiqizhixin",   fetch_jiqizhixin,    "🇨🇳 机器之心"),
    ("ithome",       fetch_ithome,        "🇨🇳 IT之家"),
    ("techcrunch_ai", fetch_techcrunch_ai, "🌍 TC"),
]


# ============================================================
#  聚合、过滤、处理
# ============================================================

def collect_articles() -> list[dict]:
    """遍历启用源，收集并去重"""
    all_articles: dict[str, dict] = {}
    yesterday = _get_yesterday()

    for src_id, fetch_fn, label in SOURCES:
        if SOURCE_SWITCH.get(src_id, "0") != "1":
            print(f"[SKIP] {label} — 已禁用")
            continue

        print(f"[FETCH] {label}...")
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
            print(f"[ERROR] {label}: {e}", file=sys.stderr)

    result = list(all_articles.values())
    result.sort(key=lambda a: a.get("published", ""), reverse=True)
    return result


def filter_yesterday(articles: list[dict]) -> list[dict]:
    """只保留昨天的文章"""
    yesterday = _get_yesterday()
    filtered = [a for a in articles if _is_yesterday(a.get("published", ""))]
    print(f"[INFO] 日期过滤：{len(articles)} → {len(filtered)} 条（昨天 {yesterday}）")
    return filtered[:30]


# ============================================================
#  邮件构建与发送
# ============================================================

def build_html(digest: str, articles: list[dict]) -> str:
    """生成摘要邮件 HTML"""
    now_str = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M")
    yesterday_str = _get_yesterday().strftime("%Y年%m月%d日")

    # 来源链接列表（去重，最多 10 条）
    seen_urls = set()
    sources_html = ""
    count = 0
    for a in articles[:MAX_SOURCES]:
        url = a.get("url", "")
        title = a.get("title_cn") or a.get("title", "")
        if url and url not in seen_urls and count < MAX_SOURCES:
            seen_urls.add(url)
            count += 1
            source = a.get("source", "来源")
            sources_html += f"""
                <li style="margin:6px 0">
                    <a href="{url}" style="color:#1a73e8">{title[:60]}</a>
                    <span style="color:#999;font-size:12px"> — {source}</span>
                </li>"""

    # 字数统计
    char_count = len(digest)

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:'Microsoft YaHei',Arial,sans-serif;max-width:650px;margin:0 auto;background:#f5f5f5">
    <div style="background:#1a73e8;color:#fff;padding:24px;text-align:center">
        <h1 style="margin:0;font-size:22px">🤖 每日 AI 新闻摘要</h1>
        <p style="margin:8px 0 0;font-size:14px;opacity:0.85">
            {yesterday_str} · {now_str} 更新 · 共 {char_count} 字
        </p>
    </div>

    <div style="background:#fff;padding:20px 24px;margin:8px 0">
        <h2 style="font-size:16px;color:#333;margin:0 0 12px">📋 昨日热点摘要</h2>
        <p style="font-size:15px;line-height:1.8;color:#333;text-indent:2em">{digest}</p>
    </div>

    <div style="background:#fff;padding:20px 24px;margin:8px 0">
        <h2 style="font-size:16px;color:#333;margin:0 0 12px">🔗 参考来源</h2>
        <ol style="padding-left:20px;font-size:14px">{sources_html}</ol>
    </div>

    <div style="text-align:center;padding:15px;color:#999;font-size:12px">
        由 GitHub Actions 每日自动发送 · 8 大新闻源聚合
    </div>
</body>
</html>"""


def send_email(html: str):
    """通过 QQ 邮箱发送"""
    yesterday_str = _get_yesterday().strftime("%m月%d日")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"AI 新闻日报 · {yesterday_str}"
    msg["From"] = QQ_EMAIL
    msg["To"] = QQ_EMAIL
    msg.attach(MIMEText(html, "html", "utf-8"))

    with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, timeout=15) as server:
        server.login(QQ_EMAIL, QQ_SMTP_CODE)
        server.sendmail(QQ_EMAIL, [QQ_EMAIL], msg.as_string())

    print(f"[OK] 邮件发送成功 → {QQ_EMAIL}")


def main():
    enabled_count = sum(1 for v in SOURCE_SWITCH.values() if v == "1")
    yesterday = _get_yesterday()
    print(f"[INFO] 启用 {enabled_count}/{len(SOURCE_SWITCH)} 个源，目标日期：{yesterday}\n")

    # 1. 收集
    articles = collect_articles()
    print(f"\n[INFO] 去重后共 {len(articles)} 条")

    # 2. 过滤昨天
    articles = filter_yesterday(articles)
    if not articles:
        print("[WARN] 昨天无新闻，发送空报")
        send_email(f"<p>{yesterday} 暂无 AI 相关新闻，明日再查看。</p>")
        return

    # 3. 翻译
    articles = translate_articles(articles)

    # 4. 生成摘要
    print("[INFO] 生成摘要...")
    digest = generate_digest(articles)
    print(f"[INFO] 摘要字数：{len(digest)}")

    # 5. 发送
    html = build_html(digest, articles)
    send_email(html)


if __name__ == "__main__":
    main()
