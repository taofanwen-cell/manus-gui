"""Day 1 单测：URL 构造 + 只读政策层 + rate limit。

覆盖维度（10 个用例，足够钉死 Day 1 交付）：
- URL 构造（中文编码、纯数字商品 ID 校验、page=1 不带 page 参数）
- parse_goods_id（多形态 URL）
- extract_keyword（自然语言，按长度倒序剥词）
- policy 允许的 read_only action
- policy 拒绝的 write action（click_element / input_text / ...）
- policy 域名白名单（拒绝第三方推广站）
- policy 关键词黑名单（拒绝"加入购物车"等误导性文本）
- rate limit 窗口生效
- rate limit 窗口滑出后恢复
- build_search_url_from_query 端到端

每个用例都对应一个真实踩坑点，注释里写明。
"""

from __future__ import annotations

import pytest

from app.ecommerce_url_query import (
    ALLOWED_PDD_HOSTS,
    SearchParams,
    build_goods_url,
    build_search_url,
    build_search_url_from_query,
    extract_keyword,
    host_is_allowed,
    parse_goods_id,
)
from app.ecommerce_policy import (
    RateLimiter,
    decision,
)


# ---------------------------------------------------------------------------
# URL 构造
# ---------------------------------------------------------------------------


class TestBuildSearchUrl:
    def test_basic_keyword(self):
        url = build_search_url(SearchParams("蓝牙耳机"))
        # 已确认：search_key 是有效参数
        assert url.startswith("https://mobile.yangkeduo.com/search_result.html")
        assert "search_key=" in url
        # 中文必须 URL 编码
        assert "%E8%93%9D%E7%89%99%E8%80%B3%E6%9C%BA" in url

    def test_page_one_omitted(self):
        """page=1 是默认值，不该被带进 query string。

        为何：拼多多 page 参数**未验证**，带上去会污染最常见的调用路径。
        只有当 page>1 时才带，降低风险面。
        """
        url = build_search_url(SearchParams("蓝牙耳机"))
        assert "page=" not in url, f"page=1 不应出现在 URL: {url}"

    def test_page_greater_than_one_kept(self):
        url = build_search_url(SearchParams("蓝牙耳机", page=3))
        assert "page=3" in url

    def test_empty_keyword_rejected(self):
        """空关键词 = 调研了一整个错误类目，宁可启动即失败。"""
        with pytest.raises(ValueError, match="keyword 不能为空"):
            build_search_url(SearchParams(""))

    def test_whitespace_keyword_rejected(self):
        with pytest.raises(ValueError, match="keyword 不能为空"):
            build_search_url(SearchParams("   "))


class TestBuildGoodsUrl:
    def test_numeric_id(self):
        url = build_goods_url(123456789)
        assert url == "https://mobile.yangkeduo.com/goods.html?goods_id=123456789"

    def test_string_numeric_id(self):
        url = build_goods_url("987654321")
        assert "goods_id=987654321" in url

    def test_non_numeric_id_rejected(self):
        """商品 ID 必须是纯数字；带字母 / 短链参数一律拒绝。"""
        with pytest.raises(ValueError, match="goods_id 必须是纯数字"):
            build_goods_url("abc123")


# ---------------------------------------------------------------------------
# parse_goods_id
# ---------------------------------------------------------------------------


class TestParseGoodsId:
    def test_standard(self):
        assert parse_goods_id("https://mobile.yangkeduo.com/goods.html?goods_id=12345") == "12345"

    def test_with_extra_params(self):
        assert parse_goods_id(
            "https://mobile.yangkeduo.com/goods.html?goods_id=12345&from=share"
        ) == "12345"

    def test_pc_path(self):
        assert parse_goods_id("https://www.pinduoduo.com/goods/67890") == "67890"

    def test_goods2_path(self):
        assert parse_goods_id("https://mobile.yangkeduo.com/goods2.html?goods_id=99999") == "99999"

    def test_unknown_url_returns_none(self):
        assert parse_goods_id("https://example.com/something") is None
        assert parse_goods_id("") is None


# ---------------------------------------------------------------------------
# extract_keyword
# ---------------------------------------------------------------------------


class TestExtractKeyword:
    def test_pure_keyword(self):
        assert extract_keyword("蓝牙耳机") == "蓝牙耳机"

    def test_with_leading_word(self):
        assert extract_keyword("帮我搜蓝牙耳机") == "蓝牙耳机"
        assert extract_keyword("查一下蓝牙耳机") == "蓝牙耳机"

    def test_with_trailing_word(self):
        assert extract_keyword("蓝牙耳机竞品分析") == "蓝牙耳机"
        assert extract_keyword("蓝牙耳机的竞品") == "蓝牙耳机"

    def test_both_sides(self):
        assert extract_keyword("帮我调研一下蓝牙耳机的竞品") == "蓝牙耳机"

    def test_long_leading_word_wins(self):
        """跨项目硬规则第 8 条：keyword 按长度倒序匹配。

        "帮我搜" 比 "搜" 长，优先剥长词。否则 "搜" in "帮我搜" 会先剥掉
        半个词，剩下 "我搜蓝牙耳机"，又得二次剥。
        """
        result = extract_keyword("帮我搜蓝牙耳机")
        assert result == "蓝牙耳机", f"实际: {result!r}"

    def test_unparseable_returns_none(self):
        """抠不出关键词就返回 None，让上层决定。绝不猜。"""
        assert extract_keyword("") is None
        assert extract_keyword("帮我看一下") is None
        assert extract_keyword("   ") is None


# ---------------------------------------------------------------------------
# 政策层
# ---------------------------------------------------------------------------


class TestPolicyAllow:
    def test_go_to_url_allowed(self):
        ok, reason = decision(
            "go_to_url",
            url="https://mobile.yangkeduo.com/search_result.html?search_key=test",
        )
        assert ok, reason

    def test_extract_content_allowed(self):
        ok, _ = decision("extract_content", current_url="https://mobile.yangkeduo.com/")
        assert ok

    def test_scroll_allowed(self):
        ok, _ = decision("scroll_down", current_url="https://mobile.yangkeduo.com/")
        assert ok


class TestPolicyReject:
    def test_click_element_blocked(self):
        """写操作一律拒绝。竞品调研是只读任务。"""
        ok, reason = decision("click_element", current_url="https://mobile.yangkeduo.com/")
        assert not ok
        assert "写操作" in reason

    def test_input_text_blocked(self):
        ok, _ = decision("input_text", current_url="https://mobile.yangkeduo.com/")
        assert not ok

    def test_gui_action_blocked(self):
        """坐标点击必须禁——vision 路线已证明这能绕过 policy。"""
        ok, _ = decision("gui_action", current_url="https://mobile.yangkeduo.com/")
        assert not ok

    def test_execute_js_blocked_even_if_readonly_looking(self):
        """execute_js 看起来只读，但能 DOM mutation，必须禁。"""
        ok, _ = decision("execute_js", current_url="https://mobile.yangkeduo.com/")
        assert not ok

    def test_unknown_action_blocked(self):
        ok, _ = decision("delete_everything", current_url="https://mobile.yangkeduo.com/")
        assert not ok

    def test_external_domain_blocked(self):
        """只允许拼多多域；推广站 / 第三方一律拒。"""
        ok, reason = decision(
            "go_to_url", url="https://promote.example.com/landing"
        )
        assert not ok
        assert "拼多多域" in reason

    def test_purchase_text_blocked(self):
        """即便 LLM 看到"加入购物车"按钮，policy 也不允许点。

        click_element 在写操作黑名单被先拦，reason 写明"写操作"。
        这是 policy 双重保护：**写操作先拦** + **关键词黑名单兜底**——
        即便未来 click_element 被误加进只读列表，关键词黑名单也能拦住。
        """
        ok, reason = decision(
            "click_element",
            current_url="https://mobile.yangkeduo.com/",
            text="加入购物车",
        )
        assert not ok
        # 写操作先于关键词检查被执行；reason 写明拦截点
        assert "写操作" in reason

    def test_follow_text_blocked(self):
        ok, _ = decision(
            "click_element",
            current_url="https://mobile.yangkeduo.com/",
            text="关注店铺",
        )
        assert not ok


# ---------------------------------------------------------------------------
# 域名白名单
# ---------------------------------------------------------------------------


class TestHostIsAllowed:
    def test_main_domain(self):
        assert host_is_allowed("https://pinduoduo.com/")
        assert host_is_allowed("https://mobile.yangkeduo.com/goods.html?goods_id=1")
        assert host_is_allowed("https://www.pinduoduo.com/goods/12345")

    def test_subdomain(self):
        assert host_is_allowed("https://fun.yangkeduo.com/")

    def test_third_party_rejected(self):
        assert not host_is_allowed("https://example.com/")
        assert not host_is_allowed("https://promote-pdd.com/")

    def test_empty_url_rejected(self):
        assert not host_is_allowed("")
        assert not host_is_allowed("not-a-url")


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------


class TestRateLimiter:
    def test_first_acquire_allowed(self):
        limiter = RateLimiter(max_per_window=3, window_seconds=60.0)
        assert limiter.acquire(now=0.0) is True

    def test_max_per_window_enforced(self):
        """第 max_per_window+1 次必须被限。"""
        limiter = RateLimiter(max_per_window=3, window_seconds=60.0)
        for i in range(3):
            assert limiter.acquire(now=float(i)) is True, f"第{i+1}次应被允许"
        # 第 4 次必须被拒
        assert limiter.acquire(now=3.0) is False

    def test_window_slides_out(self):
        """时间过了 window_seconds 之后，旧请求从窗口里滑出，新请求又被允许。"""
        limiter = RateLimiter(max_per_window=2, window_seconds=10.0)
        assert limiter.acquire(now=0.0) is True
        assert limiter.acquire(now=1.0) is True
        # 第 3 次被拒
        assert limiter.acquire(now=2.0) is False
        # 时间跳到 11s，前 2 次都滑出窗口，新请求允许
        assert limiter.acquire(now=11.0) is True

    def test_remaining_decreases(self):
        limiter = RateLimiter(max_per_window=5, window_seconds=60.0)
        assert limiter.remaining(now=0.0) == 5
        limiter.acquire(now=0.0)
        assert limiter.remaining(now=0.0) == 4
        limiter.acquire(now=1.0)
        assert limiter.remaining(now=1.0) == 3

    def test_invalid_config_rejected(self):
        with pytest.raises(ValueError, match="max_per_window 必须 > 0"):
            RateLimiter(max_per_window=0)
        with pytest.raises(ValueError, match="window_seconds 必须 > 0"):
            RateLimiter(window_seconds=0)


# ---------------------------------------------------------------------------
# 端到端
# ---------------------------------------------------------------------------


class TestEndToEnd:
    def test_build_search_url_from_query(self):
        url = build_search_url_from_query("帮我调研一下蓝牙耳机的竞品")
        assert url is not None
        assert "search_key=" in url
        assert "%E8%93%9D%E7%89%99%E8%80%B3%E6%9C%BA" in url

    def test_build_search_url_from_query_unparseable(self):
        assert build_search_url_from_query("帮我看一下") is None
        assert build_search_url_from_query("") is None
