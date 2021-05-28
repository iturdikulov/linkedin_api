import os
import sys
import pytest
import requests
from datetime import datetime

from linkedin_api.cookie_repository import (
    CookieRepository,
    LinkedinSessionExpired,
)


def mock_cookies(date=datetime.strptime("2050-05-04", "%Y-%m-%d")):
    jar = requests.cookies.RequestsCookieJar()
    jar.set(
        "JSESSIONID",
        "1234",
        expires=date.timestamp(),
        domain="httpbin.org",
        path="/cookies",
    )
    return jar


def test_save():
    pass


def test_get():
    pass


def test_get_expired():
    repo = CookieRepository()
    repo.save(
        mock_cookies(date=datetime.strptime("2001-05-04", "%Y-%m-%d")), "testuserex"
    )
    try:
        repo.get("testuserex")
        assert False
    except LinkedinSessionExpired:
        assert True
