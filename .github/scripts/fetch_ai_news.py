"""每日 AI 新闻摘要 — 多源聚合 + 中文翻译 + AI 智能摘要 + QQ 邮箱发送 + GitHub Pages 站点

新闻源（可开关）：
  API 类：GNews
  免费 API：HackerNews、DuckDuckGo
  RSS 类：36氪、机器之心、IT之家、TechCrunch
  网页抓取：百度新闻
  社区：V2EX

输出：AI 生成三板块简报 + Markdown 归档 + HTML 站点 + QQ 邮件
"""
import os
import re
import sys
import json
import smtplib
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone, timedelta, date
from typing import Callable

import requests
import xml.etree.ElementTree as ET

from deep_translator import GoogleTranslator
from duckduckgo_search import DDGS

# ---------- 加载配置 ----------
CONFIG_PATH = Path(__file__).with_name("news_config.json")

def _load_config() -> dict:
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    return {}

CONFIG = _load_config()
LLM_CONFIG = CONFIG.get("llm", {})
OUTPUT_CONFIG = CONFIG.get("output", {})
CLUSTER_CONFIG = CONFIG.get("clustering", {})
DIGEST_CONFIG = CONFIG.get("digest", {})

# ---------- 邮箱配置 ----------
QQ_EMAIL = os.environ.get("QQ_EMAIL", "")
QQ_SMTP_CODE = os.environ.get("QQ_SMTP_CODE", "")
SMTP_SERVER = "smtp.qq.com"
SMTP_PORT = 465
BEIJING_TZ = timezone(timedelta(hours=8))

# ---------- 新闻源开关（环境变量优先，否则读 config.json） ----------
def _src_enabled(src_id: str) -> str:
    """返回 '1' 或 '0'，环境变量优先"""
    env_key = f"SOURCE_{src_id.upper()}"
    env_val = os.getenv(env_key)
    if env_val is not None:
        return env_val
    cfg = CONFIG.get("sources", {}).get(src_id, {})
    return "1" if cfg.get("enabled", True) else "0"

SOURCE_SWITCH = {src_id: _src_enabled(src_id) for src_id in [
    "gnews", "ddg", "baidu", "hackernews", "36kr_ai", "jiqizhixin", "ithome", "techcrunch_ai", "v2ex"
]}

MAX_REFERENCES = CLUSTER_CONFIG.get("max_references", 50)
MIN_CLUSTER_KEYWORDS = CLUSTER_CONFIG.get("min_cluster_keywords", 2)

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
#  主题聚类 + 交叉验证 + 结论生成
# ============================================================

# 用于提取关键词的停用词（常见无意义词）
_STOP_WORDS = {"the", "a", "an", "is", "are", "was", "were", "be", "been", "in", "on",
               "at", "to", "for", "of", "with", "and", "or", "but", "not", "this", "that",
               "it", "its", "from", "by", "as", "has", "have", "had", "will", "would",
               "can", "could", "may", "might", "new", "say", "says", "said", "how", "what",
               "的", "了", "是", "在", "和", "也", "就", "都", "而", "及", "与", "着",
               "或", "一个", "没有", "我们", "他们", "它们", "这个", "那个", "不", "会",
               "可以", "已经", "还", "被", "把", "让", "更", "最", "所", "其", "等"}


def _extract_keywords(text: str) -> set[str]:
    """从文本中提取有意义的双字/三字关键词"""
    # 中英文分别处理
    words = set()
    # 英文词（≥3字母）
    for w in re.findall(r"[A-Za-z]{3,}", text):
        wl = w.lower()
        if wl not in _STOP_WORDS:
            words.add(wl)
    # 中文双字和三字组合
    chinese = re.sub(r"[^一-鿿]+", "", text)
    for i in range(len(chinese) - 1):
        bigram = chinese[i:i+2]
        if bigram not in _STOP_WORDS:
            words.add(bigram)
    for i in range(len(chinese) - 2):
        trigram = chinese[i:i+3]
        if trigram not in _STOP_WORDS:
            words.add(trigram)
    return words


def cluster_by_topic(articles: list[dict]) -> list[dict]:
    """主题聚类：将文章按共享关键词分组，同时统计跨源覆盖度"""
    clusters: list[dict] = []  # [{topic_keywords, articles, sources, score}]

    for a in articles:
        title = a.get("title_cn") or a.get("title", "")
        desc = a.get("desc_cn") or a.get("description", "")
        kw = _extract_keywords(f"{title} {desc}")

        best_cluster = None
        best_overlap = 0
        for c in clusters:
            overlap = len(kw & c["keywords"])
            if overlap >= MIN_CLUSTER_KEYWORDS and overlap > best_overlap:
                best_overlap = overlap
                best_cluster = c

        if best_cluster:
            best_cluster["articles"].append(a)
            best_cluster["keywords"] |= kw
        else:
            clusters.append({
                "keywords": kw,
                "articles": [a],
            })

    # 为每个聚类计算跨源分数
    for c in clusters:
        sources = set(a.get("source", "") for a in c["articles"])
        c["source_count"] = len(sources)
        c["source_list"] = sorted(sources)
        # 分数 = 文章数 × 不同来源数（多源验证加权）
        c["score"] = len(c["articles"]) * len(sources)
        # 提取主题标签（最高频的3个中文词）
        all_text = " ".join(a.get("title_cn") or a.get("title", "") for a in c["articles"])
        freq: dict[str, int] = {}
        for w in _extract_keywords(all_text):
            if len(w) >= 2:
                freq[w] = freq.get(w, 0) + 1
        c["top_terms"] = sorted(freq, key=freq.get, reverse=True)[:5]

    clusters.sort(key=lambda c: c["score"], reverse=True)
    return clusters


def _build_digest_prompt(date_str: str, clusters: list[dict]) -> str:
    """构建 AI 摘要 Prompt，输出三板块 Markdown 简报"""
    min_items = DIGEST_CONFIG.get("min_items_per_section", 4)
    max_items = DIGEST_CONFIG.get("max_items_per_section", 5)

    # 将聚类结果格式化为素材文本
    sections_text = []
    for c in clusters:
        for a in c["articles"]:
            title = a.get("title_cn") or a.get("title", "")
            url = a.get("url", "")
            summary = (a.get("desc_cn") or a.get("description", ""))[:150]
            source = a.get("source", "")
            sections_text.append(f"- {title} ({source}) {url} | {summary}")

    # 方括号转全角，防止 LLM 输出断裂
    materials = "\n".join(sections_text).replace("[", "［").replace("]", "］")

    return f"""请根据以下素材生成今日AI科技简报（用中文），必须严格输出 Markdown。

日期：{date_str}

输出格式要求：
- 标题行：# AI News Daily | {date_str}
- 先给 1 段导语（2-3 句）
- 接着输出 3 个二级标题段落：
  - ## 1. 今日必读：从全量素材中挑最重要/最值得关注的要点（偏"结果与影响"）
  - ## 2. 趋势与解读：挑选能代表趋势的主题，给出简短解读（偏"为什么重要"）
  - ## 3. 工具与深读：优先收录开源项目、开发工具、教程/长文（偏"怎么用/值得读什么"）
- 每个段落包含 {min_items}-{max_items} 条要点，用无序列表
- 每条要点格式：
  - [标题文字](URL) (来源)
    150字以内的内容总结
- 标题文字里不要出现半角方括号 []
- 链接必须是可点击的 Markdown 格式
- 总结必须基于下方素材中的信息，严禁编造

素材如下：
{materials}
"""


def generate_ai_digest(clusters: list[dict]) -> str:
    """调用 SiliconFlow API 生成三板块 Markdown 简报"""
    if not clusters:
        return "暂无昨日 AI 新闻。"

    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from siliconflow_client import messages_create

    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        print("[WARN] 未设置 DEEPSEEK_API_KEY，降级为规则生成", file=sys.stderr)
        return _fallback_conclusion(clusters)

    date_str = _get_yesterday().strftime("%Y-%m-%d")
    prompt = _build_digest_prompt(date_str, clusters)

    try:
        result = messages_create(
            api_key=api_key,
            base_url=LLM_CONFIG.get("base_url", "https://api.siliconflow.cn/v1"),
            model=LLM_CONFIG.get("model", "Pro/zai-org/GLM-5"),
            system="你是一个专业的AI科技新闻编辑，擅长提炼要点和趋势分析。",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=LLM_CONFIG.get("max_tokens", 2500),
            temperature=LLM_CONFIG.get("temperature", 0.6),
            timeout=LLM_CONFIG.get("timeout_seconds", 120),
            retries=LLM_CONFIG.get("retries", 3),
        )
        print(f"[INFO] AI 摘要生成成功，字数：{len(result)}")
        return result
    except Exception as e:
        print(f"[WARN] AI 摘要生成失败: {e}，降级为规则生成", file=sys.stderr)
        return _fallback_conclusion(clusters)


def _fallback_conclusion(clusters: list[dict]) -> str:
    """规则生成降级方案（AI 不可用时）"""
    if not clusters:
        return "暂无昨日 AI 新闻。"

    lines = []
    verified = [c for c in clusters if c["source_count"] >= 2]
    single = [c for c in clusters if c["source_count"] == 1]

    yesterday = _get_yesterday().strftime("%m月%d日")
    total_articles = sum(len(c["articles"]) for c in clusters)
    total_sources = len(set(a.get("source", "") for c in clusters for a in c["articles"]))

    lines.append(f"昨日（{yesterday}），AI 领域共监测到 {len(clusters)} 个热点主题，涉及 {total_articles} 篇报道，来自 {total_sources} 个独立信息源。")

    for i, c in enumerate(verified[:8], 1):
        terms = c.get("top_terms", [])[:3]
        lines.append(f"\n【{i}】{'·'.join(terms)}（{c['source_count']}个来源，{len(c['articles'])}篇）")
        for a in c["articles"][:3]:
            title = a.get("title_cn") or a.get("title", "")
            src = a.get("source", "")
            lines.append(f"  - {title}（{src}）")

    if single:
        lines.append(f"\n【其他关注】{len(single)} 个单源主题，仅供参考。")
        for c in single[:5]:
            t = (c["articles"][0].get("title_cn") or c["articles"][0].get("title", ""))[:60]
            lines.append(f"  · {t}")

    return "\n".join(lines)


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


def fetch_duckduckgo() -> list[dict]:
    """DuckDuckGo 新闻搜索 — 完全免费，无需 API Key，无需绑卡"""
    results, seen = [], set()
    queries = ["AI artificial intelligence", "大模型 AI 人工智能", "AI agent OpenAI"]
    for query in queries:
        try:
            ddgs = DDGS()
            for r in ddgs.news(query, timelimit="d", max_results=10):
                url = r.get("url", "")
                if url and url not in seen:
                    seen.add(url)
                    results.append({
                        "title": r.get("title", ""),
                        "description": r.get("body", "") or "",
                        "url": url,
                        "source": f"DuckDuckGo·{r.get('source', '')}",
                        "published": r.get("date", ""),
                    })
        except Exception as e:
            print(f"[WARN] DuckDuckGo '{query}': {e}", file=sys.stderr)
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


def fetch_v2ex() -> list[dict]:
    """V2EX 热门话题 — AI 关键词过滤"""
    v2ex_config = CONFIG.get("sources", {}).get("v2ex", {})
    if not v2ex_config.get("enabled", True):
        return []
    url = v2ex_config.get("url", "https://www.v2ex.com/api/topics/hot.json")
    limit = v2ex_config.get("limit", 20)
    ai_kw = ["AI", "人工智能", "大模型", "LLM", "GPT", "ChatGPT", "OpenAI", "Claude",
             "Gemini", "Agent", "智能体", "机器学习", "深度学习", "Copilot"]
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        results = []
        for topic in resp.json()[:limit]:
            title = topic.get("title", "")
            if any(kw.lower() in title.lower() for kw in ai_kw):
                results.append({
                    "title": title,
                    "description": (topic.get("content", "") or "")[:200],
                    "url": topic.get("url", ""),
                    "source": "V2EX",
                    "published": topic.get("created", ""),
                })
        return results
    except Exception as e:
        print(f"[WARN] V2EX: {e}", file=sys.stderr)
        return []


# ============================================================
#  源注册表
# ============================================================
SOURCES: list[tuple[str, Callable[[], list[dict]], str]] = [
    ("gnews",        fetch_gnews,         "GNews"),
    ("ddg",          fetch_duckduckgo,    "DDG"),
    ("baidu",        fetch_baidu,         "百度"),
    ("hackernews",   fetch_hackernews,    "HN"),
    ("36kr_ai",      fetch_36kr_ai,       "36氪"),
    ("jiqizhixin",   fetch_jiqizhixin,    "机器之心"),
    ("ithome",       fetch_ithome,        "IT之家"),
    ("techcrunch_ai", fetch_techcrunch_ai, "TC"),
    ("v2ex",         fetch_v2ex,          "V2EX"),
]


# ============================================================
#  归档保存
# ============================================================

def save_digest(date_str: str, markdown_text: str, sources_data: list[dict]):
    """保存简报到 digests/ 目录"""
    output_dir = Path(__file__).parent.parent.parent / OUTPUT_CONFIG.get("output_dir", "digests")
    output_dir.mkdir(parents=True, exist_ok=True)

    # 保存 Markdown
    (output_dir / f"{date_str}.md").write_text(markdown_text, encoding="utf-8")
    # 保存 latest.md
    (output_dir / "latest.md").write_text(markdown_text, encoding="utf-8")
    # 保存来源数据
    with (output_dir / f"{date_str}.sources.json").open("w", encoding="utf-8") as f:
        json.dump(sources_data, f, ensure_ascii=False, indent=2)

    print(f"[OK] 已保存到 {output_dir}")


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
    return filtered[:MAX_REFERENCES]


# ============================================================
#  邮件构建与发送
# ============================================================

def build_html(conclusion: str, clusters: list[dict]) -> str:
    """生成邮件：结论摘要 + 按主题分组的参考来源"""
    now_str = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M")
    yesterday_str = _get_yesterday().strftime("%Y年%m月%d日")

    # 统计
    all_articles = [a for c in clusters for a in c["articles"]]
    all_sources = set(a.get("source", "") for a in all_articles)
    verified_count = sum(1 for c in clusters if c["source_count"] >= 2)

    # 按主题分组渲染来源链接
    refs_html = ""
    ref_count = 0
    for ci, c in enumerate(clusters):
        if ref_count >= MAX_REFERENCES:
            break
        # 主题标签
        topic_label = " · ".join(c.get("top_terms", [])[:3])
        badge = f"✅ {c['source_count']}源验证" if c["source_count"] >= 2 else f"📌 单源"
        refs_html += f"""
            <tr>
                <td style="padding:8px 12px;background:#f0f7ff;font-weight:bold;color:#1a73e8;font-size:14px" colspan="2">
                    {badge} — {topic_label}
                </td>
            </tr>"""

        for a in c["articles"]:
            if ref_count >= MAX_REFERENCES:
                break
            url = a.get("url", "")
            title = (a.get("title_cn") or a.get("title", ""))[:60]
            source = a.get("source", "")
            if url and title:
                ref_count += 1
                refs_html += f"""
            <tr>
                <td style="padding:6px 12px;border-bottom:1px solid #f0f0f0;width:16px;text-align:center;color:#999;font-size:12px">{ref_count}</td>
                <td style="padding:6px 8px;border-bottom:1px solid #f0f0f0;font-size:13px">
                    <a href="{url}" style="color:#333;text-decoration:none">{title}</a>
                    <span style="color:#999;font-size:11px"> — {source}</span>
                </td>
            </tr>"""

    conclusion_html = conclusion.replace("\n", "<br>")

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:'Microsoft YaHei',Arial,sans-serif;max-width:700px;margin:0 auto;background:#f5f5f5">
    <div style="background:#1a73e8;color:#fff;padding:24px;text-align:center">
        <h1 style="margin:0;font-size:22px">🤖 每日 AI 新闻日报</h1>
        <p style="margin:8px 0 0;font-size:14px;opacity:0.85">
            {yesterday_str} · {now_str} 更新 · {len(clusters)}个主题 · {len(all_sources)}个来源
        </p>
    </div>

    <div style="background:#fff;padding:20px 24px;margin:8px 0">
        <h2 style="font-size:16px;color:#333;margin:0 0 12px">
            📋 今日结论
            <span style="font-size:12px;color:#999;font-weight:normal">
                （多源交叉验证：{verified_count}/{len(clusters)}个主题获跨源确认）
            </span>
        </h2>
        <div style="font-size:15px;line-height:2;color:#333">{conclusion_html}</div>
    </div>

    <div style="background:#fff;padding:20px 24px;margin:8px 0">
        <h2 style="font-size:16px;color:#333;margin:0 0 12px">🔗 参考来源（共 {ref_count} 条，按主题分组）</h2>
        <table style="width:100%;border-collapse:collapse">
            {refs_html}
        </table>
    </div>

    <div style="text-align:center;padding:15px;color:#999;font-size:12px">
        由 GitHub Actions 每日自动发送 · 8 大新闻源聚合 · 算法自动聚类
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
    date_str = yesterday.strftime("%Y-%m-%d")
    print(f"[INFO] 启用 {enabled_count}/{len(SOURCE_SWITCH)} 个源，目标日期：{yesterday}\n")

    # 1. 收集
    articles = collect_articles()
    print(f"\n[INFO] 去重后共 {len(articles)} 条")

    # 2. 过滤昨天
    articles = filter_yesterday(articles)
    if not articles:
        print("[WARN] 昨天无新闻")
        if OUTPUT_CONFIG.get("send_email", True) and QQ_EMAIL:
            send_email(f"<p>{yesterday} 暂无 AI 相关新闻，明日再查看。</p>")
        return

    # 3. 翻译
    articles = translate_articles(articles)

    # 4. 主题聚类
    print("[INFO] 主题聚类...")
    clusters = cluster_by_topic(articles)
    print(f"[INFO] 聚类完成：{len(clusters)} 个主题")
    for i, c in enumerate(clusters[:8]):
        src_badge = f"✅{c['source_count']}源" if c["source_count"] >= 2 else "📌单源"
        print(f"  {i+1}. [{src_badge}] {c.get('top_terms', [])[:3]} ({len(c['articles'])}篇)")

    # 5. AI 摘要生成
    print("[INFO] 生成 AI 摘要...")
    digest_markdown = generate_ai_digest(clusters)
    print(f"[INFO] 摘要字数：{len(digest_markdown)}")

    # 6. 归档保存
    sources_data = [a for c in clusters for a in c["articles"]]
    save_digest(date_str, digest_markdown, sources_data)

    # 7. 发送邮件（可选）
    if OUTPUT_CONFIG.get("send_email", True) and QQ_EMAIL:
        html = build_html(digest_markdown, clusters)
        send_email(html)
    else:
        print("[SKIP] 邮件发送已禁用或未配置")


if __name__ == "__main__":
    main()
