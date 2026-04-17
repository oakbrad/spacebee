"""Guard-rail unit tests added during the pre-publish security review.

Each test pins down one specific hardening from the review:
    1. PROPFIND displayname escapes XML-special chars
    2. Passthrough refuses sibling-prefix directory escapes
    3. upload_cover rejects non-https / private-IP / localhost URLs
"""

from __future__ import annotations

import pytest

from spacebee.adapters.webdav import moonreader, passthrough
from spacebee.atproto.bookhive import _is_safe_cover_url

# 1. XML escaping in PROPFIND response -------------------------------------

def test_propfind_displayname_escapes_special_chars():
    xml = moonreader._response_xml(
        href="/Books/.Moon+/Cache/Rosencrantz & Guildenstern.epub.po",
        is_collection=False,
        last_modified_http="Thu, 01 Jan 1970 00:00:00 GMT",
        content_length=0,
        etag='"abc"',
        display_name="Rosencrantz & Guildenstern <script>.epub.po",
    )
    assert "&amp;" in xml
    assert "&lt;script&gt;" in xml
    # The raw '<' must not appear as the start of a tag we didn't write.
    assert "<script>" not in xml


def test_passthrough_entry_xml_escapes_local_name(tmp_path):
    sneaky = tmp_path / "R&D <plan>.txt"
    sneaky.write_text("hello")
    xml = passthrough._entry_xml(sneaky, "/R&D <plan>.txt", tmp_path)
    assert "&amp;" in xml
    assert "&lt;plan&gt;" in xml
    assert "<plan>" not in xml


# 2. Path-traversal guard --------------------------------------------------

def test_passthrough_refuses_parent_escape(tmp_path):
    root = tmp_path / "pass"
    root.mkdir()
    p = passthrough.Passthrough(str(root))
    with pytest.raises(PermissionError):
        p._local("/../etc/passwd")


def test_passthrough_refuses_sibling_prefix_escape(tmp_path):
    # Regression for the str.startswith antipattern — a sibling like
    # `/tmp/.../pass_evil` must not pass when root is `/tmp/.../pass`.
    root = tmp_path / "pass"
    root.mkdir()
    (tmp_path / "passerby").mkdir()
    p = passthrough.Passthrough(str(root))
    with pytest.raises(PermissionError):
        p._local("/../passerby/secret")


# 3. Cover-URL SSRF guard --------------------------------------------------

@pytest.mark.parametrize(
    "url",
    [
        "https://covers.bookhive.buzz/img/abc.jpg",
        "https://images.example.com/covers/12345",
    ],
)
def test_safe_cover_urls_accepted(url):
    assert _is_safe_cover_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://covers.bookhive.buzz/img/abc.jpg",        # plaintext
        "https://localhost/foo",                          # localhost hostname
        "https://localhost.localdomain/x",                # localhost alias
        "https://foo.localhost/x",                        # .localhost suffix
        "https://127.0.0.1/x",                            # loopback IPv4
        "https://[::1]/x",                                # loopback IPv6
        "https://169.254.169.254/latest/meta-data/",      # AWS IMDS
        "https://10.0.0.5/x",                             # RFC1918
        "https://192.168.1.1/x",                          # RFC1918
        "file:///etc/passwd",                             # not http(s)
        "gopher://example.com/",                          # not http(s)
        "",                                               # empty
        "not a url at all",                               # malformed
    ],
)
def test_unsafe_cover_urls_rejected(url):
    assert not _is_safe_cover_url(url)
