"""
Dark enterprise theme for ADS Automation.
All colour, typography, and spacing constants live here.
UI components import from this module — nothing is hard-coded in component files.
"""
from __future__ import annotations

import gradio as gr

# ── Colour Palette ────────────────────────────────────────────────────────────
BACKGROUND_DARK = "#0d1117"
SURFACE_PRIMARY = "#161b22"
SURFACE_SECONDARY = "#21262d"
SURFACE_TERTIARY = "#30363d"

BORDER_DEFAULT = "#30363d"
BORDER_SUBTLE = "#21262d"

TEXT_PRIMARY = "#e6edf3"
TEXT_SECONDARY = "#8b949e"
TEXT_MUTED = "#484f58"

ACCENT_BLUE = "#1f6feb"
ACCENT_BLUE_HOVER = "#388bfd"
ACCENT_GREEN = "#238636"
ACCENT_GREEN_HOVER = "#2ea043"
ACCENT_RED = "#da3633"
ACCENT_ORANGE = "#d29922"
ACCENT_PURPLE = "#8957e5"

# ── Typography ────────────────────────────────────────────────────────────────
FONT_MONO = "'JetBrains Mono', 'Fira Code', 'Cascadia Code', 'Consolas', monospace"
FONT_SANS = "'Inter', 'Segoe UI', system-ui, -apple-system, sans-serif"
FONT_SIZE_BASE = "14px"
FONT_SIZE_SM = "12px"
FONT_SIZE_LG = "16px"
FONT_SIZE_XL = "20px"

# ── Spacing ───────────────────────────────────────────────────────────────────
RADIUS_SM = "4px"
RADIUS_MD = "8px"
RADIUS_LG = "12px"


def build_gradio_theme() -> gr.themes.Base:
    """Return a Gradio theme that approximates the dark enterprise palette."""
    return gr.themes.Base(
        primary_hue=gr.themes.Color(
            c50="#e6f0ff",
            c100="#bdd4ff",
            c200="#85aeff",
            c300="#4d88ff",
            c400="#2563eb",
            c500="#1f6feb",
            c600="#1a5dc7",
            c700="#1249a3",
            c800="#0c357f",
            c900="#06215b",
            c950="#031540",
        ),
        neutral_hue=gr.themes.Color(
            c50="#f0f6ff",
            c100="#e6edf3",
            c200="#c9d1d9",
            c300="#8b949e",
            c400="#6e7681",
            c500="#484f58",
            c600="#30363d",
            c700="#21262d",
            c800="#161b22",
            c900="#0d1117",
            c950="#010409",
        ),
        font=gr.themes.GoogleFont("Inter"),
        font_mono=gr.themes.GoogleFont("JetBrains Mono"),
    ).set(
        body_background_fill=BACKGROUND_DARK,
        body_background_fill_dark=BACKGROUND_DARK,
        block_background_fill=SURFACE_PRIMARY,
        block_background_fill_dark=SURFACE_PRIMARY,
        block_border_color=BORDER_DEFAULT,
        block_border_color_dark=BORDER_DEFAULT,
        block_title_text_color=TEXT_PRIMARY,
        block_title_text_color_dark=TEXT_PRIMARY,
        block_label_text_color=TEXT_SECONDARY,
        block_label_text_color_dark=TEXT_SECONDARY,
        input_background_fill=SURFACE_SECONDARY,
        input_background_fill_dark=SURFACE_SECONDARY,
        input_border_color=BORDER_DEFAULT,
        input_border_color_dark=BORDER_DEFAULT,
        input_placeholder_color=TEXT_MUTED,
        input_placeholder_color_dark=TEXT_MUTED,
        button_primary_background_fill=ACCENT_BLUE,
        button_primary_background_fill_dark=ACCENT_BLUE,
        button_primary_background_fill_hover=ACCENT_BLUE_HOVER,
        button_primary_background_fill_hover_dark=ACCENT_BLUE_HOVER,
        button_primary_text_color=TEXT_PRIMARY,
        button_primary_text_color_dark=TEXT_PRIMARY,
        button_secondary_background_fill=SURFACE_TERTIARY,
        button_secondary_background_fill_dark=SURFACE_TERTIARY,
        button_secondary_text_color=TEXT_PRIMARY,
        button_secondary_text_color_dark=TEXT_PRIMARY,
        checkbox_background_color=SURFACE_SECONDARY,
        checkbox_background_color_dark=SURFACE_SECONDARY,
    )


# ── Custom CSS injected into Gradio ──────────────────────────────────────────
CUSTOM_CSS = f"""
/* ── Global resets ─────────────────────────────────────────────────── */
* {{ box-sizing: border-box; }}

body, .gradio-container {{
    background: {BACKGROUND_DARK} !important;
    color: {TEXT_PRIMARY} !important;
    font-family: {FONT_SANS};
    font-size: {FONT_SIZE_BASE};
    line-height: 1.6;
}}

/* ── Remove Gradio branding / footer ─────────────────────────────────── */
footer {{ display: none !important; }}
.built-with {{ display: none !important; }}
.svelte-1kcgrtz {{ display: none !important; }}

/* ── Tighten container padding ──────────────────────────────────────── */
.gradio-container {{ max-width: 1400px !important; padding: 0 !important; margin: 0 auto !important; }}
.main {{ padding: 0 24px 24px !important; }}

/* ── Inputs and textareas ────────────────────────────────────────────── */
input, textarea, select {{
    font-family: {FONT_SANS} !important;
    font-size: {FONT_SIZE_BASE} !important;
    color: {TEXT_PRIMARY} !important;
    background: {SURFACE_SECONDARY} !important;
    border: 1px solid {BORDER_DEFAULT} !important;
    border-radius: {RADIUS_MD} !important;
    transition: border-color 0.15s !important;
}}
input:focus, textarea:focus {{
    border-color: {ACCENT_BLUE} !important;
    outline: none !important;
    box-shadow: 0 0 0 3px rgba(31,111,235,0.15) !important;
}}

/* ── Buttons ─────────────────────────────────────────────────────────── */
button.primary {{
    background: {ACCENT_BLUE} !important;
    color: #fff !important;
    border: none !important;
    border-radius: {RADIUS_MD} !important;
    font-weight: 600 !important;
    font-size: {FONT_SIZE_BASE} !important;
    padding: 10px 20px !important;
    cursor: pointer !important;
    transition: background 0.15s, transform 0.1s !important;
    letter-spacing: 0.01em !important;
}}
button.primary:hover {{ background: {ACCENT_BLUE_HOVER} !important; transform: translateY(-1px) !important; }}
button.primary:active {{ transform: translateY(0) !important; }}

button.secondary {{
    background: {SURFACE_TERTIARY} !important;
    color: {TEXT_PRIMARY} !important;
    border: 1px solid {BORDER_DEFAULT} !important;
    border-radius: {RADIUS_MD} !important;
    font-weight: 500 !important;
    transition: background 0.15s !important;
}}
button.secondary:hover {{ background: {SURFACE_SECONDARY} !important; border-color: {ACCENT_BLUE} !important; }}

/* ── Block / card containers ─────────────────────────────────────────── */
.block, .form {{
    background: {SURFACE_PRIMARY} !important;
    border: 1px solid {BORDER_DEFAULT} !important;
    border-radius: {RADIUS_LG} !important;
    box-shadow: 0 1px 3px rgba(0,0,0,0.3) !important;
}}

/* ── Labels ──────────────────────────────────────────────────────────── */
label span, .block label {{
    font-size: 11px !important;
    font-weight: 600 !important;
    letter-spacing: 0.06em !important;
    color: {TEXT_SECONDARY} !important;
    text-transform: uppercase !important;
}}

/* ── Markdown headings ───────────────────────────────────────────────── */
.prose h3, .md h3 {{
    font-size: 15px !important;
    font-weight: 600 !important;
    color: {TEXT_PRIMARY} !important;
    margin: 16px 0 8px !important;
    padding-bottom: 6px !important;
    border-bottom: 1px solid {BORDER_DEFAULT} !important;
}}
.prose p, .md p {{
    color: {TEXT_SECONDARY} !important;
    font-size: 13px !important;
    margin: 0 0 8px !important;
}}

/* ── Header bar ────────────────────────────────────────────────────── */
.ads-header {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 12px 24px;
    background: {SURFACE_PRIMARY};
    border-bottom: 1px solid {BORDER_DEFAULT};
    margin-bottom: 0;
}}
.ads-header-title {{
    font-size: 18px;
    font-weight: 600;
    color: {TEXT_PRIMARY};
    letter-spacing: -0.02em;
}}
.ads-header-badge {{
    font-size: {FONT_SIZE_SM};
    color: {TEXT_SECONDARY};
    background: {SURFACE_SECONDARY};
    padding: 2px 8px;
    border-radius: 20px;
    border: 1px solid {BORDER_DEFAULT};
}}

/* ── Sidebar ────────────────────────────────────────────────────────── */
.ads-sidebar {{
    background: {SURFACE_PRIMARY};
    border-right: 1px solid {BORDER_DEFAULT};
    min-height: 100vh;
    padding: 16px 0;
}}
.ads-session-item {{
    padding: 8px 16px;
    cursor: pointer;
    border-radius: {RADIUS_SM};
    margin: 2px 8px;
    font-size: {FONT_SIZE_SM};
    color: {TEXT_SECONDARY};
    transition: background 0.15s;
}}
.ads-session-item:hover, .ads-session-item.active {{
    background: {SURFACE_SECONDARY};
    color: {TEXT_PRIMARY};
}}

/* ── Status indicators ──────────────────────────────────────────────── */
.ads-status-dot {{
    width: 8px; height: 8px;
    border-radius: 50%;
    display: inline-block;
    margin-right: 6px;
}}
.ads-status-pending   {{ background: {TEXT_MUTED}; }}
.ads-status-running   {{ background: {ACCENT_ORANGE}; animation: pulse 1.2s infinite; }}
.ads-status-approved  {{ background: {ACCENT_GREEN}; }}
.ads-status-rejected  {{ background: {ACCENT_RED}; }}
.ads-status-complete  {{ background: {ACCENT_BLUE}; }}

@keyframes pulse {{
    0%, 100% {{ opacity: 1; }}
    50%       {{ opacity: 0.4; }}
}}

/* ── Attrition waterfall ────────────────────────────────────────────── */
.ads-step-card {{
    background: {SURFACE_PRIMARY};
    border: 1px solid {BORDER_DEFAULT};
    border-radius: {RADIUS_MD};
    padding: 12px 16px;
    margin: 4px 0;
    transition: border-color 0.15s;
}}
.ads-step-card:hover {{ border-color: {ACCENT_BLUE}; }}
.ads-step-card.approved {{ border-left: 3px solid {ACCENT_GREEN}; }}
.ads-step-card.rejected {{ border-left: 3px solid {ACCENT_RED}; }}
.ads-step-card.pending  {{ border-left: 3px solid {TEXT_MUTED}; }}

.ads-waterfall-arrow {{
    text-align: center;
    color: {TEXT_MUTED};
    font-size: 18px;
    line-height: 24px;
}}
.ads-count-badge {{
    display: inline-block;
    font-family: {FONT_MONO};
    font-size: {FONT_SIZE_SM};
    color: {ACCENT_BLUE};
    background: rgba(31,111,235,0.1);
    padding: 2px 8px;
    border-radius: {RADIUS_SM};
}}

/* ── SQL viewer ─────────────────────────────────────────────────────── */
.ads-sql-panel {{
    background: {SURFACE_SECONDARY};
    border: 1px solid {BORDER_DEFAULT};
    border-radius: {RADIUS_MD};
    overflow: hidden;
}}
.ads-sql-toolbar {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 8px 12px;
    background: {SURFACE_TERTIARY};
    border-bottom: 1px solid {BORDER_DEFAULT};
    font-size: {FONT_SIZE_SM};
    color: {TEXT_SECONDARY};
}}
textarea.ads-sql-editor {{
    font-family: {FONT_MONO} !important;
    font-size: {FONT_SIZE_SM} !important;
    background: {SURFACE_SECONDARY} !important;
    color: {TEXT_PRIMARY} !important;
    border: none !important;
    padding: 12px !important;
    min-height: 200px;
    resize: vertical;
}}

/* ── Approve / Reject buttons ────────────────────────────────────────── */
.btn-approve {{
    background: {ACCENT_GREEN} !important;
    color: #fff !important;
    border: none !important;
    font-weight: 600 !important;
}}
.btn-approve:hover {{ background: {ACCENT_GREEN_HOVER} !important; }}

.btn-reject {{
    background: transparent !important;
    color: {ACCENT_RED} !important;
    border: 1px solid {ACCENT_RED} !important;
}}
.btn-reject:hover {{ background: rgba(218,54,51,0.1) !important; }}

/* ── Progress bar ────────────────────────────────────────────────────── */
.ads-progress-bar {{
    height: 4px;
    background: {SURFACE_TERTIARY};
    border-radius: 2px;
    overflow: hidden;
}}
.ads-progress-fill {{
    height: 100%;
    background: linear-gradient(90deg, {ACCENT_BLUE}, {ACCENT_PURPLE});
    border-radius: 2px;
    transition: width 0.4s ease;
}}

/* ── Tabs ────────────────────────────────────────────────────────────── */
.tabs > .tab-nav button {{
    background: transparent !important;
    color: {TEXT_SECONDARY} !important;
    border: none !important;
    border-bottom: 2px solid transparent !important;
    font-size: {FONT_SIZE_BASE} !important;
    padding: 8px 16px !important;
}}
.tabs > .tab-nav button.selected {{
    color: {TEXT_PRIMARY} !important;
    border-bottom-color: {ACCENT_BLUE} !important;
}}

/* ── Notification toast ──────────────────────────────────────────────── */
.ads-toast {{
    position: fixed;
    top: 20px; right: 20px;
    background: {SURFACE_PRIMARY};
    border: 1px solid {BORDER_DEFAULT};
    border-radius: {RADIUS_MD};
    padding: 12px 16px;
    font-size: {FONT_SIZE_SM};
    z-index: 9999;
    box-shadow: 0 8px 24px rgba(0,0,0,0.4);
}}
"""
