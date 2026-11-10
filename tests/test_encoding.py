"""Charset handling: many Pakistani SME sites omit the Content-Type charset and only
declare it in <meta>; a few still serve legacy code pages."""

import httpx
import respx

from gtm_engine.scraping.fetcher import HttpFetcher, decode_body

URDU = "راولپنڈی کلاتھ ہاؤس"  # Rawalpindi Cloth House
BOM = b"\xef\xbb\xbf"


def test_header_charset_wins():
    assert URDU in decode_body(URDU.encode("utf-8"), "text/html; charset=utf-8")


def test_meta_charset_used_when_header_has_none():
    html = ('<html><head><meta charset="windows-1256"></head><body>' + URDU + "</body></html>").encode("cp1256", "replace")
    assert URDU.split()[1] in decode_body(html, "text/html")


def test_utf8_bom_without_declaration():
    assert URDU in decode_body(BOM + URDU.encode("utf-8"), "")


def test_undeclared_non_utf8_bytes_fall_back_instead_of_raising():
    out = decode_body(b"caf\xe9 <b>Bata</b>", "text/html")
    assert out.startswith("caf") and "Bata" in out


@respx.mock
async def test_fetcher_decodes_meta_charset_without_header(settings):
    body = b'<html><head><meta charset="iso-8859-1"></head><body>Zara Fabrics - Caf\xe9 Outlets</body></html>'
    respx.get("https://legacy.pk/").mock(return_value=httpx.Response(200, content=body, headers={"content-type": "text/html"}))
    async with HttpFetcher(settings) as fetcher:
        res = await fetcher.get("https://legacy.pk/")
    assert "Café" in res.text


@respx.mock
async def test_fetcher_treats_missing_content_type_html_as_html(settings):
    respx.get("https://noheader.pk/").mock(return_value=httpx.Response(200, content=b"<!doctype html><html><body>Hello</body></html>", headers={}))
    async with HttpFetcher(settings) as fetcher:
        res = await fetcher.get("https://noheader.pk/")
    assert res.is_html and "Hello" in res.text
