# coding=utf-8
"""
爬虫模块 - 数据抓取功能
"""

from trendradar.crawler.fetcher import DataFetcher
from trendradar.crawler.shareholder_rewards import (
    ShareholderRewardsConfig,
    ShareholderRewardsFetcher,
)

__all__ = ["DataFetcher", "ShareholderRewardsConfig", "ShareholderRewardsFetcher"]
