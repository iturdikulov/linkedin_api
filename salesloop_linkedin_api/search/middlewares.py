# uncompyle6 version 3.5.0
# Python bytecode 2.7 (62211)
# Decompiled from: Python 2.7.16 (default, Oct 17 2019, 17:14:30) 
# [GCC 4.2.1 Compatible Apple LLVM 11.0.0 (clang-1100.0.32.4) (-macos10.15-objc-s
# Embedded file name: build/bdist.linux-x86_64/egg/linkedin_sales_navigator/middlewares.py
# Compiled at: 2018-05-14 23:17:34
import re, time, random, base64
from urllib.parse import urlparse
from scrapy.exceptions import NotConfigured
from w3lib.http import basic_auth_header
from scrapy.downloadermiddlewares.retry import RetryMiddleware
from scrapy.utils.response import response_status_message
from scrapy import signals

from w3lib.http import basic_auth_header


class CustomProxyMiddleware(object):
    def process_request(self, request, spider):
        request.meta['proxy'] = spider.settings.get('PROXY_URL')
        if spider.settings.get('PROXY_USERNAME') and spider.settings.get('PROXY_PASSWORD'):
            request.headers['Proxy-Authorization'] = \
                basic_auth_header(spider.settings.get('PROXY_USERNAME'),
                                  spider.settings.get('PROXY_PASSWORD'))


class RandomUserAgent(object):
    """Randomly rotate user agents based on a list of predefined ones"""
    numReq = 0

    def __init__(self, agents):
        self.agents = agents

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler.settings.getlist('USER_AGENTS'))

    def process_request(self, request, spider):
        self.numReq += 1
        if self.numReq > 2 and self.agents:
            request.headers.setdefault('User-Agent', random.choice(self.agents))


class TooManyRequestsRetryMiddleware(RetryMiddleware):

    def __init__(self, crawler):
        super(TooManyRequestsRetryMiddleware, self).__init__(crawler.settings)
        self.crawler = crawler

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler)

    def process_response(self, request, response, spider):
        if request.meta.get('dont_retry', False):
            return response
        if response.status == 429:
            self.crawler.engine.pause()
            time.sleep(30)
            self.crawler.engine.unpause()
            reason = response_status_message(response.status)
            return self._retry(request, reason, spider) or response
        if response.status in self.retry_http_codes:
            reason = response_status_message(response.status)
            return self._retry(request, reason, spider) or response
        return response