"""
Browser tests for docs/index.html via Playwright.
Requires: pytest-playwright + `playwright install chromium`
Requires internet access for CDN scripts (D3, Apache Arrow).
"""
import re
import pytest
from playwright.sync_api import Page, expect

LOAD_TIMEOUT = 45_000  # ms — CDN fetch + ~10MB Arrow data


@pytest.fixture(autouse=True)
def app(page: Page, static_server: str):
    page.set_default_timeout(LOAD_TIMEOUT)
    page.goto(static_server)
    page.wait_for_selector("#loading", state="hidden", timeout=LOAD_TIMEOUT)


# ---------------------------------------------------------------------------
# Page load
# ---------------------------------------------------------------------------

class TestPageLoad:
    def test_title(self, page: Page):
        expect(page).to_have_title("American Income Explorer")

    def test_header_year_range(self, page: Page):
        expect(page.locator(".sub")).to_contain_text("CPS ASEC 2023–2025")

    def test_footer_year_range(self, page: Page):
        expect(page.locator("#footer")).to_contain_text("2023–2025")

    def test_dots_rendered(self, page: Page):
        assert page.locator("#chart-svg circle").count() > 100

    def test_stats_median_populated(self, page: Page):
        med = page.locator("#s-med")
        expect(med).not_to_have_text("—")
        assert "$" in med.text_content()

    def test_stats_mean_populated(self, page: Page):
        expect(page.locator("#s-mean")).not_to_have_text("—")

    def test_n_info_shows_record_count(self, page: Page):
        text = page.locator("#n-info").text_content()
        assert "survey records" in text


# ---------------------------------------------------------------------------
# Default filter state
# ---------------------------------------------------------------------------

class TestDefaultFilters:
    def test_no_default_chips(self, page: Page):
        assert page.locator("#active-chips .chip").count() == 0

    def test_clear_all_removes_active_filters(self, page: Page):
        page.locator(".tab-btn[data-tab='demo']").click()
        page.locator("#f-sex .f-btn").first.click()
        assert page.locator("#active-chips .chip").count() == 1
        page.locator("#btn-clear").click()
        assert page.locator("#active-chips .chip").count() == 0


# ---------------------------------------------------------------------------
# Filter interactions
# ---------------------------------------------------------------------------

class TestFilters:
    @pytest.fixture(autouse=True)
    def go_to_demo_tab(self, page: Page):
        page.locator(".tab-btn[data-tab='demo']").click()

    def test_filter_button_toggles_active_class(self, page: Page):
        btn = page.locator("#f-sex .f-btn").first
        expect(btn).not_to_have_class(re.compile(r"\bactive\b"))
        btn.click()
        expect(btn).to_have_class(re.compile(r"\bactive\b"))
        btn.click()
        expect(btn).not_to_have_class(re.compile(r"\bactive\b"))

    def test_filter_button_adds_chip(self, page: Page):
        before = page.locator("#active-chips .chip").count()
        page.locator("#f-sex .f-btn").first.click()
        assert page.locator("#active-chips .chip").count() == before + 1

    def test_chip_click_removes_filter(self, page: Page):
        page.locator("#f-sex .f-btn").first.click()
        assert page.locator("#active-chips .chip").count() == 1
        page.locator("#active-chips .chip").first.click()
        assert page.locator("#active-chips .chip").count() == 0

    def test_clear_all_resets_to_defaults(self, page: Page):
        page.locator("#f-sex .f-btn").first.click()
        page.locator("#btn-clear").click()
        assert page.locator("#active-chips .chip").count() == 0
        expect(page.locator("#f-sex .f-btn").first).not_to_have_class(re.compile(r"\bactive\b"))

    def test_bachelors_filter_shows_dots(self, page: Page):
        page.locator('#f-educ .f-btn[data-val="3"]').click()
        assert page.locator("#chart-svg circle").count() > 0
        expect(page.locator("#n-info")).not_to_have_text("")

    def test_housing_filter_shows_dots(self, page: Page):
        page.locator(".tab-btn[data-tab='work']").click()
        page.locator('#f-housing .f-btn[data-val="2"]').click()
        assert page.locator("#chart-svg circle").count() > 0

    def test_nonmetro_filter_shows_dots(self, page: Page):
        page.locator(".tab-btn[data-tab='geo']").click()
        page.locator('#f-metro .f-btn[data-val="0"]').click()
        assert page.locator("#chart-svg circle").count() > 0

    def test_state_filter_dims_non_state_dots(self, page: Page):
        # Selecting a state keeps all 3000 ghost dots but dims non-matching ones.
        # Kentucky (fips=21) has ~981 rows — well under 3000.
        page.locator(".tab-btn[data-tab='geo']").click()
        page.locator('#state-list .state-cb[data-fips="21"]').click()
        page.wait_for_timeout(400)
        total = page.locator("#chart-svg circle").count()
        active = page.locator("#chart-svg circle[pointer-events='all']").count()
        assert total == 3000
        assert active < 3000
        assert active > 0

    def test_first_click_sets_exclusive_filter(self, page: Page):
        page.locator("#f-age_bucket .f-btn").first.click()
        chips = page.locator("#active-chips .chip")
        assert chips.count() == 1
        expect(chips.first).to_contain_text("Age:")

    def test_second_click_clears_exclusive_filter(self, page: Page):
        btn = page.locator("#f-age_bucket .f-btn").first
        btn.click()
        assert page.locator("#active-chips .chip").count() == 1
        btn.click()
        assert page.locator("#active-chips .chip").count() == 0

    def test_multi_select_by_clicking_inactive_values(self, page: Page):
        btns = page.locator("#f-age_bucket .f-btn")
        btns.nth(0).click()
        btns.nth(1).click()
        assert page.locator("#active-chips .chip").count() == 2

    def test_click_active_in_multiselect_becomes_exclusive(self, page: Page):
        # {0, 1} → click 0 (active in multi) → {0} (exclusive)
        btns = page.locator("#f-age_bucket .f-btn")
        btns.nth(0).click()   # → {0}
        btns.nth(1).click()   # → {0, 1}
        assert page.locator("#active-chips .chip").count() == 2
        btns.nth(0).click()   # active in multi → exclusive → {0}
        assert page.locator("#active-chips .chip").count() == 1
        expect(page.locator("#active-chips .chip").first).to_contain_text("Age:")



# ---------------------------------------------------------------------------
# Color mode
# ---------------------------------------------------------------------------

class TestColorMode:
    def test_default_color_mode_income_type(self, page: Page):
        expect(page.locator(".cm-btn[data-mode='income_type']")).to_have_class(
            re.compile(r"\bactive\b")
        )

    def test_switch_color_mode_updates_button(self, page: Page):
        page.locator(".cm-btn[data-mode='work_status']").click()
        expect(page.locator(".cm-btn[data-mode='work_status']")).to_have_class(
            re.compile(r"\bactive\b")
        )
        expect(page.locator(".cm-btn[data-mode='income_type']")).not_to_have_class(
            re.compile(r"\bactive\b")
        )

    def test_legend_has_items(self, page: Page):
        assert page.locator("#legend .legend-item").count() >= 2

    def test_legend_updates_with_color_mode(self, page: Page):
        page.locator(".cm-btn[data-mode='educ']").click()
        assert page.locator("#legend .legend-item").count() >= 5

    def test_legend_always_has_topcode_entry(self, page: Page):
        expect(page.locator("#legend")).to_contain_text("Topcoded")

    def test_every_color_mode_has_filter_and_vice_versa(self, page: Page):
        # 'state' has checkbox UI (not f-state filter btns)
        SPECIAL = {'state'}
        color_modes = {
            btn.get_attribute("data-mode")
            for btn in page.locator(".cm-btn").all()
            if btn.get_attribute("data-mode") not in SPECIAL
        }
        filter_dims = {
            el.get_attribute("id").removeprefix("f-")
            for el in page.locator("[id^='f-']").all()
            if el.get_attribute("id").removeprefix("f-") not in SPECIAL
        }
        assert color_modes == filter_dims, (
            f"color-only: {color_modes - filter_dims}, "
            f"filter-only: {filter_dims - color_modes}"
        )

    def test_color_by_age_shows_8_buckets(self, page: Page):
        page.locator(".cm-btn[data-mode='age_bucket']").click()
        assert page.locator("#legend .legend-item").count() == 9

    def test_color_by_sex_shows_two_entries(self, page: Page):
        page.locator(".cm-btn[data-mode='sex']").click()
        assert page.locator("#legend .legend-item").count() == 3

    def test_color_by_sex_labels(self, page: Page):
        page.locator(".cm-btn[data-mode='sex']").click()
        text = page.locator("#legend").text_content()
        assert "Male" in text and "Female" in text

    def test_legend_click_filters_by_color_mode(self, page: Page):
        page.locator("#legend .legend-item").first.click()
        assert page.locator("#active-chips .chip").count() == 1

    def test_legend_click_clears_when_already_exclusive(self, page: Page):
        page.locator("#legend .legend-item").first.click()
        assert page.locator("#active-chips .chip").count() == 1
        page.locator("#legend .legend-item").first.click()
        assert page.locator("#active-chips .chip").count() == 0

    def test_color_by_housing_shows_entries(self, page: Page):
        page.locator(".cm-btn[data-mode='housing']").click()
        assert page.locator("#legend .legend-item").count() >= 4

    def test_color_by_roommates_shows_3_entries(self, page: Page):
        page.locator(".cm-btn[data-mode='has_roommate']").click()
        assert page.locator("#legend .legend-item").count() == 3

    def test_color_by_hh_share_shows_5_entries(self, page: Page):
        page.locator(".cm-btn[data-mode='hh_share']").click()
        assert page.locator("#legend .legend-item").count() == 5

    def test_color_mode_switch_preserves_dot_positions(self, page: Page):
        initial_cx = page.evaluate("""
            Array.from(document.querySelectorAll('#chart-svg circle'))
                .slice(0, 20).map(c => c.getAttribute('cx'))
        """)
        page.locator(".cm-btn[data-mode='work_status']").click()
        new_cx = page.evaluate("""
            Array.from(document.querySelectorAll('#chart-svg circle'))
                .slice(0, 20).map(c => c.getAttribute('cx'))
        """)
        assert initial_cx == new_cx, "Color mode switch must not reposition dots"

    def test_group_breakdown_always_visible(self, page: Page):
        # breakdown is in sidebar-stats, visible regardless of active tab
        expect(page.locator("#group-breakdown")).to_contain_text("Median")

    def test_group_breakdown_shows_count(self, page: Page):
        expect(page.locator("#group-breakdown")).to_contain_text("HH")

    def test_breakdown_visible_in_detail_tab(self, page: Page):
        page.locator(".stats-tab-btn[data-stats-tab='detail']").click()
        expect(page.locator("#stats-detail")).to_be_visible()
        expect(page.locator("#group-breakdown")).to_contain_text("Median")

    def test_breakdown_expand_toggle(self, page: Page):
        page.locator(".stats-tab-btn[data-stats-tab='detail']").click()
        btn = page.locator("#btn-expand-breakdown")
        expect(btn).to_contain_text("By group")
        expect(btn).to_contain_text("▾")
        btn.click()
        expect(btn).to_contain_text("▴")


# ---------------------------------------------------------------------------
# Tooltip
# ---------------------------------------------------------------------------

class TestTooltip:
    def test_tooltip_hidden_initially(self, page: Page):
        expect(page.locator("#tooltip")).to_be_hidden()

    def test_tooltip_shows_on_hover(self, page: Page):
        page.locator("#chart-svg circle[pointer-events='all']").first.hover(force=True)
        expect(page.locator("#tooltip")).to_be_visible()

    def test_tooltip_contains_income(self, page: Page):
        page.locator("#chart-svg circle[pointer-events='all']").first.hover(force=True)
        expect(page.locator("#tooltip .tt-income")).to_contain_text("$")

    def test_tooltip_hides_on_mouse_away(self, page: Page):
        page.locator("#chart-svg circle[pointer-events='all']").first.hover(force=True)
        expect(page.locator("#tooltip")).to_be_visible()
        page.mouse.move(5, 5)
        expect(page.locator("#tooltip")).to_be_hidden()


# ---------------------------------------------------------------------------
# State selector
# ---------------------------------------------------------------------------

class TestStateSelector:
    def _open_geo_tab(self, page: Page):
        page.locator(".tab-btn[data-tab='geo']").click()

    def test_state_list_populated(self, page: Page):
        self._open_geo_tab(page)
        assert page.locator("#state-list .state-check-label").count() >= 50

    def test_state_selection_adds_chip(self, page: Page):
        self._open_geo_tab(page)
        before = page.locator("#active-chips .chip").count()
        page.locator("#state-list .state-cb").first.click()
        chips_text = page.locator("#active-chips").text_content()
        assert "State:" in chips_text
        assert page.locator("#active-chips .chip").count() == before + 1

    def test_state_uncheck_removes_chip(self, page: Page):
        self._open_geo_tab(page)
        cb = page.locator("#state-list .state-cb").first
        cb.click()  # check
        before = page.locator("#active-chips .chip").count()
        cb.click()  # uncheck
        after = page.locator("#active-chips .chip").count()
        assert after == before - 1

    def test_state_search_filters_list(self, page: Page):
        self._open_geo_tab(page)
        page.locator("#state-search").fill("Massa")
        visible = page.evaluate("""
            Array.from(document.querySelectorAll('#state-list .state-check-label'))
                .filter(l => l.style.display !== 'none').length
        """)
        assert visible >= 1

    def test_state_chip_click_deselects(self, page: Page):
        self._open_geo_tab(page)
        page.locator("#state-list .state-cb").first.click()
        assert page.locator("#active-chips .chip").count() >= 1
        state_chip = page.locator("#active-chips .chip").filter(has_text="State:")
        state_chip.first.click()
        state_chips_after = page.locator("#active-chips .chip").filter(has_text="State:").count()
        assert state_chips_after == 0

    def test_year_filter_buttons_exist(self, page: Page):
        page.locator(".tab-btn[data-tab='survey']").click()
        assert page.locator("#f-year .f-btn").count() == 3

    def test_color_by_year_shows_three_entries(self, page: Page):
        page.locator(".cm-btn[data-mode='year']").click()
        assert page.locator("#legend .legend-item").count() == 4

    def test_all_states_represented_in_subsample(self, page: Page):
        """Every state with ≥50 rows in the Arrow file must appear in the
        3000-dot subsample so selecting it produces visible dots."""
        missing = page.evaluate("""
            () => {
                const cols = nationalTable;
                const totalRows = cols.state.length;
                const stateTotals = {};
                for (let i = 0; i < totalRows; i++) {
                    const s = cols.state[i];
                    stateTotals[s] = (stateTotals[s] || 0) + 1;
                }
                const sampledStates = new Set(dotData.map(d => cols.state[d.i]));
                const missing = [];
                for (const [fipsStr, info] of Object.entries(codebook.states || {})) {
                    const fips = parseInt(fipsStr);
                    if ((stateTotals[fips] || 0) >= 50 && !sampledStates.has(fips)) {
                        missing.push(info.name);
                    }
                }
                return missing;
            }
        """)
        assert missing == [], f"States absent from 3000-dot subsample: {missing}"


# ---------------------------------------------------------------------------
# Brush range selection
# ---------------------------------------------------------------------------

class TestBrushRange:
    def _chart_bbox(self, page: Page):
        return page.locator("#chart-svg").bounding_box()

    def _drag_brush(self, page: Page, x_frac_lo: float, x_frac_hi: float):
        """Drag a brush selection across the given fraction of the chart width."""
        bb = self._chart_bbox(page)
        y = bb["y"] + bb["height"] / 2
        x0 = bb["x"] + bb["width"] * x_frac_lo
        x1 = bb["x"] + bb["width"] * x_frac_hi
        # Activate brush mode first
        page.locator("#btn-brush").click()
        page.mouse.move(x0, y)
        page.mouse.down()
        page.mouse.move(x1, y)
        page.mouse.up()

    def test_range_button_exists(self, page: Page):
        expect(page.locator("#btn-brush")).to_be_visible()

    def test_range_button_activates_on_click(self, page: Page):
        page.locator("#btn-brush").click()
        expect(page.locator("#btn-brush")).to_have_class(re.compile(r"active"))

    def test_brush_creates_chip(self, page: Page):
        self._drag_brush(page, 0.2, 0.5)
        chips = page.locator("#active-chips .chip")
        texts = [chips.nth(i).text_content() for i in range(chips.count())]
        assert any("Range" in t for t in texts), f"No Range chip found; chips: {texts}"

    def test_brush_shows_range_stats(self, page: Page):
        self._drag_brush(page, 0.2, 0.6)
        expect(page.locator("#brush-range-info")).to_be_visible()
        expect(page.locator("#brush-range-info")).to_contain_text("Households")
        expect(page.locator("#brush-range-info")).to_contain_text("%")

    def test_brush_switches_to_range_tab(self, page: Page):
        self._drag_brush(page, 0.2, 0.6)
        expect(page.locator(".stats-tab-btn[data-stats-tab='range']")).to_have_class(re.compile(r"active"))

    def test_brush_clear_switches_back_to_stats_tab(self, page: Page):
        self._drag_brush(page, 0.2, 0.6)
        page.locator("#active-chips .chip").filter(has_text="Range").click()
        expect(page.locator(".stats-tab-btn[data-stats-tab='summary']")).to_have_class(re.compile(r"active"))

    def test_brush_stats_show_dollar_values(self, page: Page):
        self._drag_brush(page, 0.2, 0.6)
        expect(page.locator("#brush-range-info")).to_contain_text("$")

    def test_brush_chip_clear_removes_stats(self, page: Page):
        self._drag_brush(page, 0.2, 0.6)
        expect(page.locator("#brush-range-info")).to_be_visible()
        page.locator("#active-chips .chip").filter(has_text="Range").click()
        expect(page.locator("#brush-range-info")).to_be_hidden()

    def test_clear_all_removes_brush(self, page: Page):
        self._drag_brush(page, 0.2, 0.6)
        expect(page.locator("#brush-range-info")).to_be_visible()
        page.locator("#btn-clear").click()
        expect(page.locator("#brush-range-info")).to_be_hidden()

    def test_brush_range_in_url(self, page: Page):
        self._drag_brush(page, 0.2, 0.6)
        url = page.url
        assert "brush_lo=" in url and "brush_hi=" in url, f"Brush not in URL: {url}"

    def test_brush_range_restores_from_url(self, page: Page, static_server: str):
        page.goto(static_server + "#brush_lo=50000&brush_hi=150000")
        page.wait_for_selector("#loading", state="hidden", timeout=LOAD_TIMEOUT)
        expect(page.locator("#brush-range-info")).to_be_visible()
        expect(page.locator("#brush-range-info")).to_contain_text("$50,000")

    def test_range_expand_button_hidden_without_range(self, page: Page):
        page.locator(".stats-tab-btn[data-stats-tab='range']").click()
        expect(page.locator("#btn-expand-range")).to_be_hidden()

    def test_range_expand_button_visible_after_brush(self, page: Page):
        self._drag_brush(page, 0.2, 0.6)
        expect(page.locator("#btn-expand-range")).to_be_visible()

    def test_range_expand_shows_breakdown(self, page: Page):
        self._drag_brush(page, 0.2, 0.6)
        expect(page.locator("#range-breakdown")).to_be_hidden()
        page.locator("#btn-expand-range").click()
        expect(page.locator("#range-breakdown")).to_be_visible()
        expect(page.locator("#range-breakdown")).to_contain_text("Median")

    def test_range_expand_button_toggles_label(self, page: Page):
        self._drag_brush(page, 0.2, 0.6)
        btn = page.locator("#btn-expand-range")
        expect(btn).to_contain_text("▾")
        btn.click()
        expect(btn).to_contain_text("▴")


# ---------------------------------------------------------------------------
# View toggle (Dots / Bars / 100%)
# ---------------------------------------------------------------------------

class TestViewToggle:
    def test_three_view_buttons_exist(self, page: Page):
        assert page.locator(".view-btn").count() == 3

    def test_dots_button_active_by_default(self, page: Page):
        expect(page.locator(".view-btn[data-view='dots']")).to_have_class(re.compile(r"\bactive\b"))

    def test_bars_view_renders_rects(self, page: Page):
        page.locator(".view-btn[data-view='bars']").click()
        page.wait_for_timeout(300)
        assert page.locator("#chart-svg rect").count() > 0
        assert page.locator("#chart-svg circle").count() == 0

    def test_pct_view_renders_rects(self, page: Page):
        page.locator(".view-btn[data-view='pct']").click()
        page.wait_for_timeout(300)
        assert page.locator("#chart-svg rect").count() > 0

    def test_switching_back_to_dots_renders_circles(self, page: Page):
        # Regression: switching bars→dots used updateDotOpacity() on empty SVG
        page.locator(".view-btn[data-view='bars']").click()
        page.wait_for_timeout(300)
        page.locator(".view-btn[data-view='dots']").click()
        page.wait_for_timeout(300)
        assert page.locator("#chart-svg circle").count() > 100

    def test_pct_then_back_to_dots(self, page: Page):
        page.locator(".view-btn[data-view='pct']").click()
        page.wait_for_timeout(300)
        page.locator(".view-btn[data-view='dots']").click()
        page.wait_for_timeout(300)
        assert page.locator("#chart-svg circle").count() > 100

    def test_color_mode_change_in_bars_rerenders(self, page: Page):
        page.locator(".view-btn[data-view='bars']").click()
        page.wait_for_timeout(300)
        rect_count_before = page.locator("#chart-svg rect").count()
        page.locator(".cm-btn[data-mode='work_status']").click()
        page.wait_for_timeout(300)
        rect_count_after = page.locator("#chart-svg rect").count()
        assert rect_count_after > 0, "Bars disappeared after color mode change"

    def test_bars_view_in_url(self, page: Page):
        page.locator(".view-btn[data-view='bars']").click()
        page.wait_for_timeout(300)
        assert "v=bars" in page.url

    def test_pct_view_in_url(self, page: Page):
        page.locator(".view-btn[data-view='pct']").click()
        page.wait_for_timeout(300)
        assert "v=pct" in page.url

    def test_dots_view_not_in_url(self, page: Page):
        # 'dots' is default — should not clutter URL
        assert "v=" not in page.url

    def test_view_restored_from_url(self, page: Page, static_server: str):
        page.goto(static_server + "#v=bars")
        page.wait_for_selector("#loading", state="hidden", timeout=LOAD_TIMEOUT)
        expect(page.locator(".view-btn[data-view='bars']")).to_have_class(re.compile(r"\bactive\b"))
        page.wait_for_timeout(300)
        assert page.locator("#chart-svg rect").count() > 0

    def test_bars_yaxis_shows_millions(self, page: Page):
        page.locator(".view-btn[data-view='bars']").click()
        page.wait_for_timeout(300)
        text = page.locator("#chart-svg").text_content()
        assert "M" in text

    def test_pct_yaxis_shows_percent(self, page: Page):
        page.locator(".view-btn[data-view='pct']").click()
        page.wait_for_timeout(300)
        text = page.locator("#chart-svg").text_content()
        assert "%" in text


# ---------------------------------------------------------------------------
# Bar hover tooltips
# ---------------------------------------------------------------------------

class TestBarTooltip:
    def _switch_to_bars(self, page: Page):
        page.locator(".view-btn[data-view='bars']").click()
        page.wait_for_timeout(300)

    def test_bar_tooltip_hidden_initially(self, page: Page):
        self._switch_to_bars(page)
        expect(page.locator("#tooltip")).to_be_hidden()

    def test_bar_tooltip_shows_on_hover(self, page: Page):
        self._switch_to_bars(page)
        page.locator("#chart-svg rect[style*='cursor']").first.hover(force=True)
        expect(page.locator("#tooltip")).to_be_visible()

    def test_bar_tooltip_contains_income_range(self, page: Page):
        self._switch_to_bars(page)
        page.locator("#chart-svg rect[style*='cursor']").first.hover(force=True)
        expect(page.locator("#tooltip .tt-income")).to_contain_text("$")

    def test_bar_tooltip_contains_dash_separator(self, page: Page):
        self._switch_to_bars(page)
        page.locator("#chart-svg rect[style*='cursor']").first.hover(force=True)
        expect(page.locator("#tooltip .tt-income")).to_contain_text("–")

    def test_bar_tooltip_contains_households(self, page: Page):
        self._switch_to_bars(page)
        page.locator("#chart-svg rect[style*='cursor']").first.hover(force=True)
        expect(page.locator("#tooltip")).to_contain_text("households")

    def test_bar_tooltip_contains_pct_of_band(self, page: Page):
        self._switch_to_bars(page)
        page.locator("#chart-svg rect[style*='cursor']").first.hover(force=True)
        expect(page.locator("#tooltip")).to_contain_text("% of band")

    def test_bar_tooltip_hides_on_mouse_away(self, page: Page):
        self._switch_to_bars(page)
        page.locator("#chart-svg rect[style*='cursor']").first.hover(force=True)
        expect(page.locator("#tooltip")).to_be_visible()
        page.mouse.move(5, 5)
        expect(page.locator("#tooltip")).to_be_hidden()

    def test_pct_view_tooltip_shows(self, page: Page):
        page.locator(".view-btn[data-view='pct']").click()
        page.wait_for_timeout(300)
        page.locator("#chart-svg rect[style*='cursor']").first.hover(force=True)
        expect(page.locator("#tooltip")).to_be_visible()
        expect(page.locator("#tooltip")).to_contain_text("% of band")


# ---------------------------------------------------------------------------
# Mobile layout  (iPhone 12 emulation, 390×844 viewport)
# ---------------------------------------------------------------------------

@pytest.fixture
def mobile_page(browser, static_server: str):
    ctx = browser.new_context(
        viewport={"width": 390, "height": 844},
        device_scale_factor=3,
        is_mobile=True,
        has_touch=True,
    )
    pg = ctx.new_page()
    pg.set_default_timeout(45_000)
    pg.goto(static_server)
    pg.wait_for_selector("#loading", state="hidden", timeout=45_000)
    yield pg
    ctx.close()


class TestMobile:
    def test_sidebar_hidden_by_default(self, mobile_page: Page):
        # Sidebar is off-screen (drawer closed) — not visually visible
        sidebar = mobile_page.locator("#sidebar")
        box = sidebar.bounding_box()
        assert box is None or box["y"] >= 844, "Sidebar should be off-screen when drawer is closed"

    def test_filters_button_visible(self, mobile_page: Page):
        expect(mobile_page.locator("#btn-filters")).to_be_visible()

    def test_filters_button_opens_drawer(self, mobile_page: Page):
        mobile_page.locator("#btn-filters").tap()
        mobile_page.wait_for_timeout(400)
        expect(mobile_page.locator("#sidebar")).to_be_visible()

    def test_close_button_closes_drawer(self, mobile_page: Page):
        mobile_page.locator("#btn-filters").tap()
        mobile_page.wait_for_timeout(400)
        mobile_page.locator("#btn-close-sidebar").tap()
        mobile_page.wait_for_timeout(400)
        sidebar = mobile_page.locator("#sidebar")
        box = sidebar.bounding_box()
        assert box is None or box["y"] >= 844

    def test_mobile_stats_strip_visible(self, mobile_page: Page):
        expect(mobile_page.locator("#mobile-stats")).to_be_visible()

    def test_mobile_stats_shows_median(self, mobile_page: Page):
        expect(mobile_page.locator("#mobile-stats")).to_contain_text("Median")
        expect(mobile_page.locator("#mobile-stats")).to_contain_text("$")

    def test_mobile_stats_shows_household_count(self, mobile_page: Page):
        expect(mobile_page.locator("#mobile-stats")).to_contain_text("HH")

    def test_dots_render_on_mobile(self, mobile_page: Page):
        circles = mobile_page.locator("#chart-svg circle")
        assert circles.count() > 0

    def test_fewer_dots_on_mobile(self, mobile_page: Page):
        mobile_page.wait_for_timeout(300)
        count = mobile_page.locator("#chart-svg circle").count()
        assert count <= 800, f"Expected ≤800 dots on mobile, got {count}"

    def test_dot_tap_shows_tooltip(self, mobile_page: Page):
        mobile_page.locator("#chart-svg circle").first.tap(force=True)
        expect(mobile_page.locator("#tooltip")).to_be_visible()

    def test_dot_tap_tooltip_contains_income(self, mobile_page: Page):
        mobile_page.locator("#chart-svg circle").first.tap(force=True)
        expect(mobile_page.locator("#tooltip .tt-income")).to_contain_text("$")

    def test_filter_works_on_mobile(self, mobile_page: Page):
        mobile_page.locator("#btn-filters").tap()
        mobile_page.wait_for_timeout(300)
        mobile_page.locator(".tab-btn[data-tab='demo']").tap()
        mobile_page.wait_for_timeout(200)
        mobile_page.locator("#f-sex .f-btn").first.tap()
        mobile_page.wait_for_timeout(400)
        mobile_page.locator("#btn-close-sidebar").tap()
        mobile_page.wait_for_timeout(300)
        expect(mobile_page.locator("#active-chips .chip")).to_have_count(1)

    def test_view_toggle_works_on_mobile(self, mobile_page: Page):
        mobile_page.locator(".view-btn[data-view='bars']").tap()
        mobile_page.wait_for_timeout(300)
        assert mobile_page.locator("#chart-svg rect").count() > 0

    def test_bar_tap_shows_tooltip(self, mobile_page: Page):
        mobile_page.locator(".view-btn[data-view='bars']").tap()
        mobile_page.wait_for_timeout(300)
        mobile_page.locator("#chart-svg rect[style*='cursor']").first.tap(force=True)
        expect(mobile_page.locator("#tooltip")).to_be_visible()

    def test_footer_hidden_on_mobile(self, mobile_page: Page):
        expect(mobile_page.locator("#footer")).to_be_hidden()
