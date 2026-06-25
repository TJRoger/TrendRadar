# coding=utf-8
"""
上市公司股东回馈活动抓取器

从巨潮资讯公告检索股东回馈相关公告，并转换为 RSSData，复用现有
RSS 存储、关键词统计、HTML 报告和通知链路。
"""

import html
import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin

import pytz
import requests

from trendradar.storage.base import RSSData, RSSItem
from trendradar.utils.time import DEFAULT_TIMEZONE, get_configured_time, is_within_days


@dataclass
class ShareholderRewardsConfig:
    """股东回馈活动抓取配置"""

    id: str = "shareholder-rewards-cninfo"
    name: str = "上市公司股东回馈活动"
    enabled: bool = True
    keywords: List[str] = field(default_factory=lambda: ["股东回馈", "回馈股东", "投资者回馈"])
    title_include_keywords: List[str] = field(
        default_factory=lambda: ["股东回馈", "回馈股东", "投资者回馈", "回馈投资者", "全体股东派送福利"]
    )
    title_exclude_keywords: List[str] = field(default_factory=lambda: ["收购", "评估报告"])
    max_items: int = 30
    max_age_days: int = 90
    request_interval: int = 800
    timeout: int = 15
    use_proxy: bool = False
    proxy_url: str = ""
    timezone: str = DEFAULT_TIMEZONE
    api_url: str = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
    detail_base_url: str = "https://static.cninfo.com.cn/"
    column: str = "szse"
    page_size: int = 30


class ShareholderRewardsFetcher:
    """抓取最新上市公司股东回馈活动公告"""

    DEFAULT_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Origin": "https://www.cninfo.com.cn",
        "Referer": "https://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice",
        "X-Requested-With": "XMLHttpRequest",
    }

    def __init__(self, config: ShareholderRewardsConfig):
        self.config = config
        self.session = self._create_session()

    def _create_session(self) -> requests.Session:
        session = requests.Session()
        session.headers.update(self.DEFAULT_HEADERS)

        if self.config.use_proxy and self.config.proxy_url:
            session.proxies = {
                "http": self.config.proxy_url,
                "https": self.config.proxy_url,
            }

        return session

    def _date_range(self) -> str:
        if self.config.max_age_days <= 0:
            return ""

        now = get_configured_time(self.config.timezone)
        start = now - timedelta(days=self.config.max_age_days)
        return f"{start.strftime('%Y-%m-%d')}~{now.strftime('%Y-%m-%d')}"

    def _query_keyword(self, keyword: str) -> Tuple[List[Dict], Optional[str]]:
        payload = {
            "pageNum": "1",
            "pageSize": str(max(1, self.config.page_size)),
            "column": self.config.column,
            "tabName": "fulltext",
            "plate": "",
            "stock": "",
            "searchkey": keyword,
            "secid": "",
            "category": "",
            "trade": "",
            "seDate": self._date_range(),
            "sortName": "",
            "sortType": "",
            "isHLtitle": "true",
        }

        try:
            response = self.session.post(
                self.config.api_url,
                data=payload,
                timeout=self.config.timeout,
            )
            response.raise_for_status()
            data = response.json()
            announcements = data.get("announcements") or []
            print(f"[股东回馈] 关键词 {keyword}: 获取 {len(announcements)} 条")
            return announcements, None
        except requests.Timeout:
            return [], f"请求超时 ({self.config.timeout}s)"
        except requests.RequestException as e:
            return [], f"请求失败: {e}"
        except ValueError as e:
            return [], f"响应解析失败: {e}"

    def _clean_text(self, value: str) -> str:
        if not value:
            return ""
        value = html.unescape(str(value))
        value = re.sub(r"<[^>]+>", "", value)
        value = re.sub(r"\s+", " ", value)
        return value.strip()

    def _timestamp_to_iso(self, timestamp_ms: Optional[int]) -> str:
        if not timestamp_ms:
            return ""

        try:
            tz = pytz.timezone(self.config.timezone)
        except pytz.UnknownTimeZoneError:
            tz = pytz.timezone(DEFAULT_TIMEZONE)

        try:
            return datetime.fromtimestamp(int(timestamp_ms) / 1000, tz).isoformat()
        except (TypeError, ValueError, OSError):
            return ""

    def _announcement_url(self, announcement: Dict) -> str:
        adjunct_url = announcement.get("adjunctUrl") or ""
        if adjunct_url:
            return urljoin(self.config.detail_base_url, adjunct_url)

        announcement_id = announcement.get("announcementId") or ""
        if announcement_id:
            return f"https://www.cninfo.com.cn/new/disclosure/detail?announcementId={announcement_id}"

        return ""

    def _should_keep(self, title: str, published_at: str) -> bool:
        title_lower = title.lower()

        include_keywords = [kw.lower() for kw in self.config.title_include_keywords if kw]
        if include_keywords and not any(keyword in title_lower for keyword in include_keywords):
            return False

        exclude_keywords = [kw.lower() for kw in self.config.title_exclude_keywords if kw]
        if exclude_keywords and any(keyword in title_lower for keyword in exclude_keywords):
            return False

        if self.config.max_age_days > 0 and published_at:
            return is_within_days(published_at, self.config.max_age_days, self.config.timezone)

        return True

    def _to_rss_item(self, announcement: Dict, crawl_time: str, keyword: str) -> Optional[RSSItem]:
        sec_code = self._clean_text(announcement.get("secCode", ""))
        sec_name = self._clean_text(announcement.get("secName", "") or announcement.get("tileSecName", ""))
        raw_title = announcement.get("announcementTitle", "") or announcement.get("shortTitle", "")
        announcement_title = self._clean_text(raw_title)
        if not announcement_title:
            return None

        published_at = self._timestamp_to_iso(announcement.get("announcementTime"))
        if not self._should_keep(announcement_title, published_at):
            return None

        company_label = sec_name
        if sec_code:
            company_label = f"{sec_name}({sec_code})" if sec_name else sec_code

        title = f"{company_label}: {announcement_title}" if company_label else announcement_title
        announcement_id = announcement.get("announcementId") or ""
        summary_parts = [
            f"关键词: {keyword}",
            f"公告编号: {announcement_id}" if announcement_id else "",
            f"公告类型: {announcement.get('adjunctType', '')}" if announcement.get("adjunctType") else "",
        ]
        summary = "；".join(part for part in summary_parts if part)

        return RSSItem(
            title=title,
            feed_id=self.config.id,
            feed_name=self.config.name,
            url=self._announcement_url(announcement),
            published_at=published_at,
            summary=summary,
            author="巨潮资讯",
            crawl_time=crawl_time,
            first_time=crawl_time,
            last_time=crawl_time,
            count=1,
        )

    def fetch_all(self) -> RSSData:
        now = get_configured_time(self.config.timezone)
        crawl_time = now.strftime("%H:%M")
        crawl_date = now.strftime("%Y-%m-%d")

        all_items: List[RSSItem] = []
        seen_keys = set()
        errors = []

        keywords = [keyword.strip() for keyword in self.config.keywords if keyword and keyword.strip()]
        print(f"[股东回馈] 开始抓取 {len(keywords)} 个关键词...")

        for index, keyword in enumerate(keywords):
            if index > 0:
                interval = self.config.request_interval / 1000
                jitter = random.uniform(-0.2, 0.2) * interval
                time.sleep(max(0, interval + jitter))

            announcements, error = self._query_keyword(keyword)
            if error:
                print(f"[股东回馈] 关键词 {keyword}: {error}")
                errors.append(error)
                continue

            for announcement in announcements:
                unique_key = announcement.get("announcementId") or announcement.get("adjunctUrl")
                if not unique_key:
                    unique_key = (
                        announcement.get("secCode", ""),
                        announcement.get("announcementTitle", ""),
                        announcement.get("announcementTime", ""),
                    )

                if unique_key in seen_keys:
                    continue
                seen_keys.add(unique_key)

                item = self._to_rss_item(announcement, crawl_time, keyword)
                if item:
                    all_items.append(item)

        all_items.sort(key=lambda item: item.published_at or "", reverse=True)
        if self.config.max_items > 0:
            all_items = all_items[: self.config.max_items]

        failed_ids = [self.config.id] if errors and not all_items else []
        print(f"[股东回馈] 抓取完成: {len(all_items)} 条")

        return RSSData(
            date=crawl_date,
            crawl_time=crawl_time,
            items={self.config.id: all_items} if all_items else {},
            id_to_name={self.config.id: self.config.name},
            failed_ids=failed_ids,
        )
