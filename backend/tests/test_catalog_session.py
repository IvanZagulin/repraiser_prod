"""Catalog requests must look like the browser that collected the WB session."""

import importlib
from unittest.mock import patch

# app.services экспортирует под этим именем экземпляр парсера, нужен сам модуль
cp = importlib.import_module("app.services.catalog_parser")

REQUEST_TXT = (
    "curl 'https://www.wildberries.ru/__internal/u-catalog/sellers/v4/catalog?dest=12' \\\n"
    "  -H 'accept: */*' \\\n"
    "  -H 'Accept-Language: ru-RU,ru;q=0.9' \\\n"
    "  -H 'cookie: _wbauid=111; x_wbaas_token=1.1000.abc' \\\n"
    "  -H 'deviceid: site_deadbeef' \\\n"
    "  -H 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36' \\\n"
    "  -H 'x-spa-version: 14.25.3'\n"
)


def _with_request_file(tmp_path, text=REQUEST_TXT):
    f = tmp_path / "request.txt"
    f.write_text(text, encoding="utf-8")
    return patch.object(cp, "AUTH_REQUEST_PATHS", [str(f)])


def test_only_browser_headers_and_cookies(tmp_path):
    with _with_request_file(tmp_path):
        headers = cp.get_headers()
        cookies = cp.get_cookies()
    assert cookies == {"_wbauid": "111", "x_wbaas_token": "1.1000.abc"}
    # один user-agent — из сессии, без зашитого Edge и без токена покупателя
    assert [k for k in headers if k.lower() == "user-agent"] == ["user-agent"]
    assert "Chrome/131" in headers["user-agent"]
    assert "authorization" not in headers
    assert headers["deviceid"] == "site_deadbeef"
    assert headers["accept-language"] == "ru-RU,ru;q=0.9"  # имя приведено к нижнему регистру
    assert "cookie" not in headers


def test_impersonate_follows_session_chrome_version(tmp_path):
    with _with_request_file(tmp_path), \
         patch.object(cp, "_supported_chrome_versions", return_value=[120, 124, 131, 136]):
        assert cp.get_impersonate() == "chrome131"
    with patch.object(cp, "_supported_chrome_versions", return_value=[120, 124]):
        assert cp.get_impersonate({"user-agent": "... Chrome/131.0.0.0 ..."}) == "chrome124"
    assert cp.get_impersonate({"user-agent": "curl/8"}) == cp._DEFAULT_IMPERSONATE


def test_missing_session_file_falls_back_without_secrets(tmp_path):
    with patch.object(cp, "AUTH_REQUEST_PATHS", [str(tmp_path / "nope.txt")]):
        assert cp.get_cookies() == {}
        headers = cp.get_headers()
    assert "authorization" not in headers
    assert "user-agent" not in headers
