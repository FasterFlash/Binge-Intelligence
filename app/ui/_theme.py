"""
Netflix dark theme CSS for Streamlit — v2 with animations + polish.

Palette:
    #0a0a0a   deepest black (vignette edge)
    #141414   true black (bg)
    #1a1a1a   lift
    #1f1f1f   card
    #2a2a2a   border
    #E50914   Netflix red
    #B20710   deeper red (hover)
    #FFFFFF   text
    #B3B3B3   dim text
    #808080   dimmer
"""

from __future__ import annotations

import streamlit as st

NETFLIX_RED = "#E50914"
NETFLIX_RED_DEEP = "#B20710"
BG = "#141414"
BG_DEEP = "#0a0a0a"
BG_LIFT = "#1a1a1a"
CARD = "#1f1f1f"
BORDER = "#2a2a2a"
TEXT = "#FFFFFF"
DIM = "#B3B3B3"
DIMMER = "#808080"


_CSS = f"""
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap');

  /* ---------- Base ---------- */
  html, body, [data-testid="stAppViewContainer"] {{
      background: {BG} !important;
      color: {TEXT} !important;
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif !important;
  }}
  /* Radial vignette for depth */
  [data-testid="stAppViewContainer"] {{
      background:
        radial-gradient(ellipse 1200px 800px at 50% 0%,
                        rgba(229, 9, 20, 0.08) 0%,
                        transparent 50%),
        radial-gradient(ellipse at center,
                        {BG_LIFT} 0%,
                        {BG} 40%,
                        {BG_DEEP} 100%) !important;
  }}

  [data-testid="stSidebar"] {{ background-color: {BG_DEEP} !important; }}
  [data-testid="stHeader"] {{ background: transparent !important; visibility: hidden; }}

  * {{ color: {TEXT}; }}
  p, span, label, div {{ color: {TEXT}; }}

  /* ---------- Hero / section typography ---------- */
  .bi-hero {{
      font-size: 42px; font-weight: 800; letter-spacing: -0.025em;
      color: {TEXT}; margin: 6px 0 4px 0; line-height: 1.08;
      animation: biFadeInDown 0.7s cubic-bezier(0.16, 1, 0.3, 1) both;
  }}
  .bi-hero-sm {{
      font-size: 28px; font-weight: 800; letter-spacing: -0.02em;
      color: {TEXT}; margin: 6px 0 4px 0; line-height: 1.1;
      animation: biFadeInDown 0.7s cubic-bezier(0.16, 1, 0.3, 1) both;
  }}
  .bi-sub {{
      color: {DIM}; font-size: 14px; margin-bottom: 24px; max-width: 560px;
      animation: biFadeIn 0.9s 0.15s ease-out both;
  }}
  .bi-section {{
      font-size: 10px; font-weight: 700; letter-spacing: 0.18em;
      text-transform: uppercase; color: {DIM};
      margin: 22px 0 10px 0; padding-left: 10px;
      border-left: 2px solid {NETFLIX_RED};
      animation: biFadeIn 0.5s ease-out both;
  }}

  /* ---------- Keyframes ---------- */
  @keyframes biFadeInDown {{
      0% {{ opacity: 0; transform: translateY(-16px); }}
      100% {{ opacity: 1; transform: translateY(0); }}
  }}
  @keyframes biFadeInUp {{
      0% {{ opacity: 0; transform: translateY(16px); }}
      100% {{ opacity: 1; transform: translateY(0); }}
  }}
  @keyframes biFadeIn {{
      0% {{ opacity: 0; }}
      100% {{ opacity: 1; }}
  }}
  @keyframes biPulse {{
      0%, 100% {{ transform: scale(1); opacity: 1; }}
      50% {{ transform: scale(1.08); opacity: 0.85; }}
  }}
  @keyframes biShimmer {{
      0% {{ background-position: -200% 0; }}
      100% {{ background-position: 200% 0; }}
  }}
  @keyframes biSlideInLeft {{
      0% {{ opacity: 0; transform: translateX(-24px); }}
      100% {{ opacity: 1; transform: translateX(0); }}
  }}

  /* ---------- Netflix "B" logo tile ---------- */
  .bi-logo {{
      display: inline-flex; align-items: center; justify-content: center;
      width: 36px; height: 36px; border-radius: 7px;
      background: linear-gradient(135deg, {NETFLIX_RED} 0%, {NETFLIX_RED_DEEP} 100%);
      color: white; font-weight: 900; font-size: 20px;
      margin-right: 10px; vertical-align: middle;
      box-shadow: 0 4px 12px rgba(229, 9, 20, 0.35);
      transition: transform 0.2s ease;
  }}
  .bi-logo:hover {{ transform: scale(1.05); }}
  .bi-logo-text {{
      font-weight: 800; font-size: 18px; letter-spacing: -0.02em;
      vertical-align: middle;
  }}
  .bi-brandbar {{
      padding-top: 8px;
      animation: biFadeInDown 0.5s ease-out both;
  }}
  .bi-logo-sm {{ width: 28px !important; height: 28px !important; font-size: 15px !important; }}
  .bi-logo-text-sm {{ font-size: 15px !important; }}

  /* Chat header — compact, intimate, more "chat" less "dashboard" */
  .bi-chat-header {{
      display: flex; align-items: center; justify-content: space-between;
      padding: 10px 4px 14px 4px;
      border-bottom: 1px solid {BORDER};
      margin-bottom: 18px;
      animation: biFadeInDown 0.5s ease-out both;
  }}
  .bi-chat-header-left {{ display: flex; align-items: center; gap: 10px; }}
  .bi-chat-ctx {{
      display: flex; flex-direction: column; gap: 2px;
  }}
  .bi-chat-ctx-name {{
      font-size: 14px; font-weight: 700; color: {TEXT}; line-height: 1.2;
  }}
  .bi-chat-ctx-sub {{
      font-size: 11px; color: {DIM}; line-height: 1.2;
  }}
  .bi-chat-dot {{
      display: inline-block; width: 6px; height: 6px; border-radius: 50%;
      background: #22c55e; margin-right: 6px; vertical-align: middle;
      box-shadow: 0 0 6px rgba(34,197,94,0.6);
  }}

  /* ---------- User cards (picker) ---------- */
  /* Whole card is a hyperlink → kill underline / inherited link color */
  a.bi-user-link, a.bi-user-link:visited, a.bi-user-link:hover {{
      text-decoration: none !important;
      color: inherit !important;
      display: block;
      cursor: pointer;
  }}
  .bi-user-card {{
      background: linear-gradient(145deg, {CARD} 0%, {BG_LIFT} 100%);
      border: 1px solid {BORDER};
      border-radius: 12px; padding: 20px; margin-bottom: 12px;
      transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1);
      position: relative; overflow: hidden;
      animation: biFadeInUp 0.5s ease-out both;
  }}
  .bi-user-card::before {{
      content: ""; position: absolute; top: 0; left: 0; right: 0;
      height: 2px; background: {NETFLIX_RED};
      transform: scaleX(0); transform-origin: left;
      transition: transform 0.3s ease;
  }}
  .bi-user-card:hover {{
      border-color: {NETFLIX_RED};
      transform: translateY(-4px);
      box-shadow: 0 12px 32px rgba(229, 9, 20, 0.2),
                  0 0 0 1px rgba(229, 9, 20, 0.3);
  }}
  .bi-user-card:hover::before {{ transform: scaleX(1); }}

  .bi-badge {{
      display: inline-block; padding: 3px 11px; border-radius: 100px;
      font-size: 10px; font-weight: 700; letter-spacing: 0.08em;
      text-transform: uppercase;
      background: {NETFLIX_RED}; color: white;
  }}
  .bi-badge-ghost {{
      background: transparent; border: 1px solid {BORDER}; color: {DIM};
  }}
  .bi-user-name {{
      font-size: 18px; font-weight: 700; margin: 12px 0 4px 0;
      letter-spacing: -0.01em;
  }}
  .bi-user-meta {{ color: {DIM}; font-size: 13px; margin-bottom: 10px; }}
  .bi-user-taste {{ color: {DIMMER}; font-size: 12px; line-height: 1.5; }}

  /* ---------- Poster cards ---------- */
  .bi-poster {{
      background: linear-gradient(135deg, var(--c1, #333) 0%, var(--c2, #111) 100%);
      border-radius: 10px; padding: 20px; min-height: 220px;
      display: flex; flex-direction: column; justify-content: space-between;
      border: 1px solid {BORDER}; position: relative; overflow: hidden;
      transition: all 0.25s cubic-bezier(0.16, 1, 0.3, 1);
      animation: biFadeInUp 0.5s ease-out both;
  }}
  /* When a real TMDB poster is loaded, taller aspect + no bottom-overlay
     fade (the dark linear-gradient is already baked into the inline style). */
  .bi-poster.bi-poster-img {{
      min-height: 300px;
      background-color: {BG_DEEP};
      padding: 14px;
  }}
  .bi-poster.bi-poster-img::after {{ display: none; }}
  .bi-poster::after {{
      content: ""; position: absolute; inset: 0;
      background: linear-gradient(180deg, transparent 50%, rgba(0,0,0,0.6) 100%);
      pointer-events: none;
  }}
  .bi-poster:hover {{
      transform: translateY(-5px) scale(1.02);
      border-color: {NETFLIX_RED};
      box-shadow: 0 16px 40px rgba(0,0,0,0.5),
                  0 0 0 1px rgba(229, 9, 20, 0.4);
  }}
  .bi-poster-title {{
      font-size: 19px; font-weight: 800; line-height: 1.15;
      color: white; letter-spacing: -0.01em;
      position: relative; z-index: 1;
  }}
  .bi-poster-year {{
      font-size: 12px; color: rgba(255,255,255,0.75);
      margin-top: 4px; font-weight: 500;
      position: relative; z-index: 1;
  }}
  .bi-poster-maturity {{
      position: absolute; top: 12px; right: 12px;
      font-size: 10px; font-weight: 700; padding: 3px 7px;
      border-radius: 3px; background: rgba(0,0,0,0.7);
      color: white; letter-spacing: 0.05em;
      z-index: 2;
  }}
  .bi-chip {{
      display: inline-block; font-size: 10px; font-weight: 600;
      padding: 3px 9px; border-radius: 100px;
      background: rgba(255,255,255,0.15); color: white;
      margin: 2px 3px 0 0; letter-spacing: 0.03em;
      backdrop-filter: blur(8px);
      position: relative; z-index: 1;
  }}

  /* ---------- Prompt chip buttons ---------- */
  .stButton > button {{
      background: {CARD} !important; color: {TEXT} !important;
      border: 1px solid {BORDER} !important;
      border-radius: 100px !important;
      padding: 11px 20px !important;
      font-size: 13.5px !important; font-weight: 500 !important;
      transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1) !important;
      box-shadow: none !important;
      text-align: left !important;
      white-space: normal !important; line-height: 1.3 !important;
      animation: biFadeInUp 0.5s ease-out both;
  }}
  .stButton > button:hover {{
      border-color: {NETFLIX_RED} !important;
      background: linear-gradient(135deg, #2a1518 0%, #1a1a1a 100%) !important;
      transform: translateY(-2px) !important;
      box-shadow: 0 6px 20px rgba(229, 9, 20, 0.25) !important;
  }}
  .stButton > button:active {{ transform: translateY(0) !important; }}
  .stButton > button:focus {{
      box-shadow: 0 0 0 2px {NETFLIX_RED}66 !important;
      outline: none !important;
  }}

  /* Stagger chip entry */
  .stButton:nth-child(1) > button {{ animation-delay: 0.05s; }}
  .stButton:nth-child(2) > button {{ animation-delay: 0.10s; }}
  .stButton:nth-child(3) > button {{ animation-delay: 0.15s; }}
  .stButton:nth-child(4) > button {{ animation-delay: 0.20s; }}
  .stButton:nth-child(5) > button {{ animation-delay: 0.25s; }}

  /* ---------- Primary CTA button ---------- */
  .bi-primary .stButton > button {{
      background: linear-gradient(135deg, {NETFLIX_RED} 0%, {NETFLIX_RED_DEEP} 100%) !important;
      color: white !important;
      border-color: transparent !important;
      font-weight: 700 !important;
      padding: 12px 28px !important;
      text-align: center !important;
      letter-spacing: 0.02em !important;
      box-shadow: 0 4px 16px rgba(229, 9, 20, 0.4) !important;
  }}
  .bi-primary .stButton > button:hover {{
      background: linear-gradient(135deg, #f6121d 0%, {NETFLIX_RED} 100%) !important;
      transform: translateY(-2px) !important;
      box-shadow: 0 8px 24px rgba(229, 9, 20, 0.55) !important;
  }}

  /* ---------- Custom chat bubbles (hand-rolled HTML, not st.chat_message) ----
     Streamlit's st.chat_message doesn't give us reliable role selectors, so
     we build bubbles ourselves. Each message is a flex row:
       - User:      [spacer] ------------ [bubble][avatar]   (right-aligned)
       - Assistant: [avatar][bubble] ------------ [spacer]   (left-aligned)
     Bubbles are inline-block → they hug content, capped at 72% column width. */

  .bi-row {{
      display: flex; align-items: flex-end; gap: 8px;
      margin: 6px 0;
      animation: biFadeInUp 0.25s ease-out both;
      width: 100%;
  }}
  .bi-row-user {{ justify-content: flex-end; }}
  .bi-row-ai   {{ justify-content: flex-start; }}

  .bi-avatar {{
      width: 32px; height: 32px;
      border-radius: 7px;
      display: flex; align-items: center; justify-content: center;
      font-weight: 800; font-size: 14px; color: white;
      flex-shrink: 0;
      margin-bottom: 2px;
  }}
  .bi-avatar-ai {{
      background: linear-gradient(135deg, {NETFLIX_RED} 0%, {NETFLIX_RED_DEEP} 100%);
      box-shadow: 0 2px 6px rgba(229, 9, 20, 0.4);
  }}
  .bi-avatar-user {{
      background: {BORDER};
      color: {DIM};
      border: 1px solid #3a3a3a;
  }}

  .bi-bubble {{
      max-width: 72%;
      padding: 9px 14px;
      border-radius: 16px;
      font-size: 14.5px;
      line-height: 1.5;
      word-wrap: break-word;
      overflow-wrap: break-word;
      box-shadow: 0 1px 4px rgba(0,0,0,0.25);
      color: {TEXT};
  }}
  .bi-bubble-ai {{
      background: {CARD};
      border: 1px solid {BORDER};
      border-top-left-radius: 4px;   /* tail toward the avatar */
  }}
  .bi-bubble-user {{
      background: linear-gradient(135deg, #2a1518 0%, #1f1215 100%);
      border: 1px solid rgba(229, 9, 20, 0.3);
      border-top-right-radius: 4px;  /* tail toward the avatar */
  }}

  /* Markdown-rendered content inside bubbles */
  .bi-bubble p {{ margin: 0; }}
  .bi-bubble p + p {{ margin-top: 8px; }}
  .bi-bubble em {{ color: {TEXT}; font-style: italic; }}
  .bi-bubble strong {{ color: white; font-weight: 700; }}
  .bi-bubble code {{
      background: rgba(255,255,255,0.08);
      padding: 1px 6px; border-radius: 4px;
      font-family: 'SF Mono', Monaco, monospace;
      font-size: 0.9em;
  }}
  .bi-bubble ul {{
      margin: 6px 0 0 0; padding-left: 20px;
  }}
  .bi-bubble li {{ margin: 2px 0; }}

  /* Hide the stock st.chat_message chrome inside the chat view since we
     render our own bubbles. */
  .bi-chat-view [data-testid="stChatMessage"] {{ display: none !important; }}

  /* ---------- Chat input — tight compact pill ----------
     Streamlit stacks several wrappers around the chat_input, each with its
     own padding. We zero them ALL out and let the textarea itself be the
     only thing with visual presence. */

  /* Zero out every wrapper's background, border, shadow, padding, margin */
  [data-testid="stBottom"],
  [data-testid="stBottom"] > div,
  [data-testid="stBottom"] > div > div,
  [data-testid="stBottomBlockContainer"],
  [data-testid="stBottomBlockContainer"] > div,
  [data-testid="stBottomBlockContainer"] > div > div,
  [data-testid="stChatInput"],
  [data-testid="stChatInput"] > div,
  [data-testid="stChatInput"] > div > div,
  [data-testid="stChatInputContainer"],
  [data-testid="stChatInputContainer"] > div,
  section[data-testid="stChatInput"],
  div[data-testid="stChatFloatingInputContainer"],
  .stChatFloatingInputContainer {{
      background: transparent !important;
      border: none !important;
      box-shadow: none !important;
  }}
  [data-testid="stBottom"] {{ background: {BG} !important; }}
  /* Fade above the input */
  [data-testid="stBottom"]::before {{
      content: ""; position: absolute; left: 0; right: 0;
      top: -32px; height: 32px; pointer-events: none;
      background: linear-gradient(180deg, transparent 0%, {BG} 100%);
  }}
  /* Narrow + tight bottom-block container, with breathing room from the
     viewport bottom so the pill doesn't glue to the edge of the screen. */
  [data-testid="stBottomBlockContainer"] {{
      max-width: 760px !important;
      margin: 0 auto !important;
      padding-top: 6px !important;
      padding-bottom: 22px !important;
      padding-left: 14px !important; padding-right: 14px !important;
  }}
  /* Zero margins/padding on every inner wrapper of the chat input,
     AND keep the direct children as a relative-positioning context so the
     submit button can be absolutely-positioned on top of the textarea
     rather than wrapping to the next line. */
  [data-testid="stChatInput"],
  [data-testid="stChatInput"] > div,
  [data-testid="stChatInput"] > div > div,
  [data-testid="stChatInputContainer"] {{
      padding: 0 !important;
      margin: 0 !important;
      min-height: 0 !important;
      position: relative !important;
  }}

  /* The textarea itself — the only thing with visual weight */
  [data-testid="stChatInput"] textarea,
  [data-testid="stChatInputContainer"] textarea {{
      background: {CARD} !important;
      color: {TEXT} !important;
      border: 1px solid {BORDER} !important;
      border-radius: 20px !important;
      padding: 7px 44px 7px 18px !important;   /* right pad = space for button */
      font-size: 14px !important;
      line-height: 1.4 !important;
      min-height: 38px !important;
      max-height: 120px !important;
      box-shadow: none !important;
      transition: border-color 0.2s ease, box-shadow 0.2s ease !important;
      resize: none !important;
  }}
  [data-testid="stChatInput"] textarea:focus {{
      border-color: {NETFLIX_RED} !important;
      box-shadow: 0 0 0 2px rgba(229, 9, 20, 0.2) !important;
      outline: none !important;
  }}
  [data-testid="stChatInput"] textarea::placeholder {{
      color: {DIMMER} !important; font-weight: 400;
  }}

  /* Submit button: small red circle ANCHORED inside the pill via absolute
     positioning. Streamlit's default layout puts it as a block sibling of
     the textarea, which wraps to a new line once we zero out container
     padding — hence this override. */
  [data-testid="stChatInput"] button {{
      background: linear-gradient(135deg, {NETFLIX_RED} 0%, {NETFLIX_RED_DEEP} 100%) !important;
      border: none !important;
      border-radius: 50% !important;
      width: 28px !important; height: 28px !important;
      min-width: 28px !important; min-height: 28px !important;
      box-shadow: none !important;
      transition: all 0.15s ease !important;
      padding: 0 !important;
      margin: 0 !important;

      /* ---- absolutely positioned inside the pill ---- */
      position: absolute !important;
      right: 6px !important;
      top: 50% !important;
      transform: translateY(-50%) !important;
      z-index: 2;
  }}
  [data-testid="stChatInput"] button svg {{
      fill: white !important; color: white !important;
      width: 12px !important; height: 12px !important;
  }}
  [data-testid="stChatInput"] button:hover {{
      transform: translateY(-50%) scale(1.08) !important;
      box-shadow: 0 2px 8px rgba(229, 9, 20, 0.5) !important;
  }}

  /* ---------- Form controls ---------- */
  [data-baseweb="select"] > div,
  [data-baseweb="select"] [data-baseweb="tag"] {{
      background: {CARD} !important;
      border-color: {BORDER} !important;
      color: {TEXT} !important;
      transition: border-color 0.2s ease;
  }}
  [data-baseweb="select"] > div:hover {{ border-color: {NETFLIX_RED} !important; }}
  [data-baseweb="select"] input,
  [data-baseweb="select"] span {{ color: {TEXT} !important; }}

  [data-baseweb="tag"] {{
      background: {NETFLIX_RED} !important;
      border-color: {NETFLIX_RED} !important;
      color: white !important;
  }}
  [data-baseweb="tag"] svg {{ fill: white !important; }}

  /* Dropdown popover (portal-rendered).
     DO NOT apply CSS transforms or animations here — BaseWeb positions the
     popover with inline top/left in a transform-stacking context, so any
     `transform` we add visually detaches it from its anchor box. Opacity
     fade only. */
  div[data-baseweb="popover"],
  div[data-baseweb="menu"],
  div[data-baseweb="menu"] ul,
  div[data-baseweb="popover"] ul {{
      background: {CARD} !important;
      border: 1px solid {BORDER} !important;
      color: {TEXT} !important;
      border-radius: 10px !important;
      box-shadow: 0 8px 32px rgba(0,0,0,0.6) !important;
  }}
  /* Soft opacity fade only (no transform) */
  @keyframes biPopFade {{
      from {{ opacity: 0; }} to {{ opacity: 1; }}
  }}
  div[data-baseweb="popover"] {{
      animation: biPopFade 0.12s ease-out;
  }}
  div[data-baseweb="popover"] *,
  div[data-baseweb="menu"] * {{
      color: {TEXT} !important;
      background-color: transparent !important;
  }}
  li[role="option"],
  div[data-baseweb="menu"] li {{
      background: transparent !important;
      color: {TEXT} !important;
      padding: 10px 14px !important;
      transition: background 0.15s ease;
  }}
  li[role="option"]:hover,
  div[data-baseweb="menu"] li:hover,
  li[aria-selected="true"] {{
      background: rgba(229, 9, 20, 0.15) !important;
      color: {NETFLIX_RED} !important;
  }}

  /* Text input + number input */
  .stTextInput input, .stNumberInput input {{
      background: {CARD} !important;
      color: {TEXT} !important;
      border-color: {BORDER} !important;
      border-radius: 8px !important;
      transition: border-color 0.2s ease;
  }}
  .stTextInput input:focus, .stNumberInput input:focus {{
      border-color: {NETFLIX_RED} !important;
      box-shadow: 0 0 0 2px rgba(229, 9, 20, 0.2) !important;
  }}

  /* ---- Slider ----
     Streamlit's slider is BaseWeb's Slider; the TRACK is a single div whose
     background is set INLINE to a linear-gradient painting both the filled
     portion (primaryColor) and the unfilled portion (gray) in one gradient.
     Overriding `background` on that div kills the gradient and paints the
     whole track one color — exactly the bug we just hit.
     Fix: don't touch the track. Only style thumbs. The fill color comes from
     Streamlit's `primaryColor` theme setting in .streamlit/config.toml,
     which we point at Netflix red there. */
  [data-testid="stSlider"] div[role="slider"] {{
      background: {NETFLIX_RED} !important;
      border: 2px solid {NETFLIX_RED} !important;
      box-shadow: 0 2px 8px rgba(229, 9, 20, 0.45) !important;
  }}
  /* Value tooltip bubble + min/max tick labels */
  [data-testid="stSlider"] [data-baseweb="slider"] [role="slider"] + div,
  [data-testid="stTickBarMin"], [data-testid="stTickBarMax"] {{
      color: {DIM} !important;
      background: transparent !important;
  }}
  /* Slider tooltip value above the thumb — keep it light on dark */
  [data-testid="stSlider"] [data-baseweb="tooltip"] {{
      background: {CARD} !important; color: {TEXT} !important;
      border: 1px solid {BORDER} !important;
  }}

  /* Widget labels */
  label, [data-testid="stWidgetLabel"] p {{
      color: {DIM} !important;
      font-size: 11px !important;
      font-weight: 700 !important;
      text-transform: uppercase;
      letter-spacing: 0.12em !important;
  }}

  /* Captions */
  .stCaption, [data-testid="stCaptionContainer"] {{
      color: {DIMMER} !important;
  }}

  /* ---------- Hide Streamlit chrome ---------- */
  #MainMenu, footer, header[data-testid="stHeader"] {{ visibility: hidden; height: 0 !important; }}
  .stDeployButton, [data-testid="stToolbar"] {{ display: none !important; }}

  /* Divider */
  hr {{ border-color: {BORDER} !important; opacity: 0.5; }}

  /* ---------- Spinner (pulsing B instead of generic) ---------- */
  [data-testid="stSpinner"] {{
      display: flex; align-items: center; gap: 12px;
  }}
  [data-testid="stSpinner"] svg {{
      stroke: {NETFLIX_RED} !important;
      fill: none !important;
      animation: biPulse 1.2s ease-in-out infinite;
  }}
  [data-testid="stSpinner"] div {{ color: {DIM} !important; font-weight: 500; }}

  /* ---------- Error alerts ---------- */
  [data-testid="stAlert"] {{
      background: {CARD} !important;
      border: 1px solid {NETFLIX_RED} !important;
      border-left: 3px solid {NETFLIX_RED} !important;
      border-radius: 8px !important;
      color: {TEXT} !important;
  }}

  /* ---------- Thinking indicator (bot "typing" bubble as an AI row) ---- */
  .bi-thinking-row {{
      display: flex; align-items: flex-end; gap: 8px;
      margin: 6px 0;
      animation: biFadeInUp 0.25s ease-out both;
      width: 100%; justify-content: flex-start;
  }}
  .bi-thinking {{
      background: {CARD};
      border: 1px solid {BORDER};
      border-radius: 16px;
      border-top-left-radius: 4px;
      padding: 10px 14px;
      display: flex; align-items: center; gap: 12px;
      max-width: 380px;
      box-shadow: 0 1px 4px rgba(0,0,0,0.25);
      font-size: 13.5px;
  }}
  .bi-thinking-dots {{
      display: inline-flex; gap: 5px; flex-shrink: 0;
  }}
  .bi-thinking-dots span {{
      width: 7px; height: 7px; border-radius: 50%;
      background: {NETFLIX_RED};
      animation: biDotPulse 1.4s infinite ease-in-out both;
  }}
  .bi-thinking-dots span:nth-child(2) {{ animation-delay: 0.2s; }}
  .bi-thinking-dots span:nth-child(3) {{ animation-delay: 0.4s; }}
  @keyframes biDotPulse {{
      0%, 80%, 100% {{ transform: scale(0.55); opacity: 0.4; }}
      40% {{ transform: scale(1); opacity: 1; }}
  }}

  /* Rotating thinking text — 4 lines on a 12s cycle, each visible ~2s */
  .bi-thinking-text {{
      position: relative; height: 22px;
      flex: 1; overflow: hidden;
      color: {DIM}; font-size: 14px; font-weight: 500;
  }}
  .bi-thinking-text span {{
      position: absolute; top: 0; left: 0; right: 0;
      opacity: 0; transform: translateY(8px);
      animation: biRotateLine 12s infinite;
      white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }}
  .bi-thinking-text span:nth-child(1) {{ animation-delay: 0s; }}
  .bi-thinking-text span:nth-child(2) {{ animation-delay: 3s; }}
  .bi-thinking-text span:nth-child(3) {{ animation-delay: 6s; }}
  .bi-thinking-text span:nth-child(4) {{ animation-delay: 9s; }}
  @keyframes biRotateLine {{
      0%   {{ opacity: 0; transform: translateY(8px); }}
      5%   {{ opacity: 1; transform: translateY(0); }}
      20%  {{ opacity: 1; transform: translateY(0); }}
      25%  {{ opacity: 0; transform: translateY(-8px); }}
      100% {{ opacity: 0; transform: translateY(-8px); }}
  }}

  /* ---------- Content width ---------- */
  .block-container {{ padding-top: 2rem !important; max-width: 1400px !important; }}

  /* =========================================================================
     DEV AUDIT DASHBOARD — components used by streamlit_app.py (observability)
     ========================================================================= */

  /* KPI stat tile */
  .bi-kpi {{
      background: linear-gradient(145deg, {CARD} 0%, {BG_LIFT} 100%);
      border: 1px solid {BORDER};
      border-radius: 12px;
      padding: 18px 20px;
      position: relative; overflow: hidden;
      transition: all 0.2s ease;
      animation: biFadeInUp 0.4s ease-out both;
  }}
  .bi-kpi::before {{
      content: ""; position: absolute; top: 0; left: 0;
      width: 3px; height: 100%;
      background: var(--kpi-accent, {NETFLIX_RED});
  }}
  .bi-kpi:hover {{ transform: translateY(-2px); border-color: #3a3a3a; }}
  .bi-kpi-label {{
      font-size: 10px; text-transform: uppercase; letter-spacing: 0.14em;
      color: {DIMMER}; font-weight: 700; margin-bottom: 10px;
  }}
  .bi-kpi-value {{
      font-size: 30px; font-weight: 800; letter-spacing: -0.02em;
      color: {TEXT}; line-height: 1;
  }}
  .bi-kpi-sub {{
      font-size: 12px; color: {DIM}; margin-top: 6px; font-weight: 500;
  }}
  .bi-kpi-sub-good {{ color: #22c55e !important; }}
  .bi-kpi-sub-warn {{ color: #f59e0b !important; }}
  .bi-kpi-sub-bad  {{ color: {NETFLIX_RED} !important; }}

  /* ---- Session card (grid, click anywhere to inspect) ---- */
  a.bi-sess-link, a.bi-sess-link:visited, a.bi-sess-link:hover {{
      text-decoration: none !important; color: inherit !important;
      display: block; cursor: pointer;
  }}
  .bi-sess-card {{
      background: linear-gradient(145deg, {CARD} 0%, {BG_LIFT} 100%);
      border: 1px solid {BORDER};
      border-radius: 12px;
      padding: 16px 18px;
      min-height: 180px;
      display: flex; flex-direction: column; gap: 10px;
      transition: all 0.25s cubic-bezier(0.16, 1, 0.3, 1);
      position: relative; overflow: hidden;
      animation: biFadeInUp 0.4s ease-out both;
      margin-bottom: 12px;
  }}
  .bi-sess-card::before {{
      content: ""; position: absolute; top: 0; left: 0; right: 0;
      height: 2px; background: {NETFLIX_RED};
      transform: scaleX(0); transform-origin: left;
      transition: transform 0.3s ease;
  }}
  .bi-sess-card:hover {{
      border-color: {NETFLIX_RED};
      transform: translateY(-3px);
      box-shadow: 0 12px 28px rgba(229, 9, 20, 0.2),
                  0 0 0 1px rgba(229, 9, 20, 0.25);
  }}
  .bi-sess-card:hover::before {{ transform: scaleX(1); }}

  .bi-sess-arch {{
      font-size: 10px; font-weight: 700;
      text-transform: uppercase; letter-spacing: 0.14em;
      color: {DIMMER};
  }}
  .bi-sess-user {{
      font-size: 16px; font-weight: 700; color: {TEXT};
      letter-spacing: -0.01em; line-height: 1.2;
  }}
  .bi-sess-user-sub {{ color: {DIM}; font-size: 12px; margin-top: 2px; }}
  .bi-sess-preview {{
      color: {DIM}; font-size: 13px; line-height: 1.4;
      display: -webkit-box; -webkit-line-clamp: 2;
      -webkit-box-orient: vertical; overflow: hidden;
      font-style: italic;
      border-left: 2px solid {BORDER};
      padding-left: 10px;
      margin-top: 2px;
  }}
  .bi-sess-metrics {{
      display: flex; gap: 14px; margin-top: auto;
      padding-top: 10px; border-top: 1px solid {BORDER};
      align-items: center; flex-wrap: wrap;
  }}
  .bi-sess-metric {{
      display: flex; flex-direction: column; gap: 1px;
  }}
  .bi-sess-metric-value {{
      color: {TEXT}; font-weight: 800; font-size: 15px; line-height: 1;
  }}
  .bi-sess-metric-label {{
      color: {DIMMER}; font-size: 9px;
      text-transform: uppercase; letter-spacing: 0.1em;
  }}
  .bi-sess-time {{
      color: {DIMMER}; font-size: 11px; margin-left: auto;
      text-align: right;
  }}
  .bi-sess-fail-chip {{
      display: inline-block; font-size: 10px; font-weight: 700;
      padding: 2px 7px; border-radius: 100px;
      background: rgba(229, 9, 20, 0.15); color: {NETFLIX_RED};
      border: 1px solid rgba(229, 9, 20, 0.3);
      text-transform: uppercase; letter-spacing: 0.05em;
  }}

  /* Session detail — transcript + event timeline */
  .bi-detail-head {{
      background: {CARD};
      border: 1px solid {BORDER};
      border-left: 3px solid {NETFLIX_RED};
      border-radius: 10px;
      padding: 16px 20px;
      margin-bottom: 20px;
  }}
  .bi-event {{
      background: {CARD};
      border: 1px solid {BORDER};
      border-left: 3px solid var(--event-accent, {DIMMER});
      border-radius: 8px;
      padding: 10px 14px;
      margin-bottom: 6px;
      font-size: 12.5px;
  }}
  .bi-event-head {{
      display: flex; justify-content: space-between; align-items: center;
      gap: 10px;
  }}
  .bi-event-type {{
      font-weight: 700; font-size: 11px;
      text-transform: uppercase; letter-spacing: 0.08em;
      color: var(--event-accent, {TEXT});
  }}
  .bi-event-time {{ color: {DIMMER}; font-size: 11px; }}
  .bi-event-body {{ margin-top: 6px; color: {DIM}; font-size: 12.5px; }}
  .bi-event-code {{
      font-family: 'SF Mono', Monaco, monospace;
      font-size: 11.5px;
      background: {BG_DEEP};
      padding: 6px 10px; border-radius: 5px; margin-top: 6px;
      color: {TEXT}; display: block; white-space: pre-wrap;
      max-height: 160px; overflow-y: auto;
  }}

  /* Time-window pill filter */
  .bi-filter-row {{
      display: flex; gap: 8px; margin: 10px 0 24px 0;
      flex-wrap: wrap;
  }}

  /* =========================================================================
     CONSUMER CHAT — history drawer, follow-up chips, continue-watching rail
     ========================================================================= */

  /* History drawer */
  .bi-history-wrap {{
      background: {BG_LIFT};
      border: 1px solid {BORDER};
      border-radius: 12px;
      padding: 14px 16px;
      margin-bottom: 16px;
      animation: biFadeInDown 0.25s ease-out both;
      max-height: 420px;
      overflow-y: auto;
  }}
  .bi-date-group {{
      font-size: 10px; font-weight: 700;
      text-transform: uppercase; letter-spacing: 0.14em;
      color: {DIMMER};
      margin: 10px 0 6px 0;
      padding-bottom: 4px;
      border-bottom: 1px solid {BORDER};
  }}
  .bi-date-group:first-child {{ margin-top: 0; }}

  a.bi-history-link, a.bi-history-link:visited, a.bi-history-link:hover {{
      text-decoration: none !important; color: inherit !important;
      display: block; cursor: pointer;
  }}
  .bi-history-item {{
      display: flex; align-items: center; gap: 10px;
      padding: 8px 10px; border-radius: 8px;
      transition: background 0.15s ease;
      margin-bottom: 2px;
  }}
  .bi-history-item:hover {{ background: {CARD}; }}
  .bi-history-item.active {{
      background: rgba(229, 9, 20, 0.12);
      border-left: 2px solid {NETFLIX_RED};
  }}
  .bi-history-title {{
      flex: 1; color: {TEXT}; font-size: 13px; font-weight: 500;
      overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }}
  .bi-history-meta {{
      color: {DIMMER}; font-size: 11px; flex-shrink: 0;
  }}
  .bi-history-turns {{
      display: inline-block; padding: 1px 7px; border-radius: 100px;
      background: {CARD}; color: {DIM}; font-size: 10px; font-weight: 600;
  }}

  /* Follow-up chip row */
  .bi-followup-wrap {{
      margin: 10px 0 10px 42px;  /* indent to align with assistant avatar */
      display: flex; flex-wrap: wrap; gap: 6px;
      animation: biFadeIn 0.4s 0.2s ease-out both;
  }}
  .bi-followup-label {{
      font-size: 10px; color: {DIMMER};
      text-transform: uppercase; letter-spacing: 0.12em;
      font-weight: 600;
      width: 100%; margin-bottom: 4px;
  }}

  /* Make follow-up buttons smaller than starter chips */
  .bi-followup-wrap + div .stButton > button,
  div[data-testid="stHorizontalBlock"]:has(> div > div > .bi-followup-marker) .stButton > button {{
      padding: 6px 12px !important;
      font-size: 12.5px !important;
      border-radius: 100px !important;
  }}

  /* Last Watched rail — vertical poster tiles */
  a.bi-lw-link, a.bi-lw-link:visited, a.bi-lw-link:hover {{
      text-decoration: none !important; color: inherit !important;
      display: block; cursor: pointer;
  }}
  .bi-lw-tile {{
      position: relative; overflow: hidden;
      border-radius: 10px;
      aspect-ratio: 2 / 3;
      background: linear-gradient(135deg, var(--c1, #333) 0%, var(--c2, #111) 100%);
      background-size: cover; background-position: center;
      border: 1px solid {BORDER};
      transition: all 0.25s cubic-bezier(0.16, 1, 0.3, 1);
      animation: biFadeInUp 0.4s ease-out both;
      cursor: pointer;
  }}
  .bi-lw-tile:hover {{
      transform: translateY(-4px) scale(1.03);
      border-color: {NETFLIX_RED};
      box-shadow: 0 14px 30px rgba(0,0,0,0.6),
                  0 0 0 1px rgba(229, 9, 20, 0.4);
      z-index: 5;
  }}
  .bi-lw-tile::after {{
      content: ""; position: absolute; inset: 0;
      background: linear-gradient(180deg,
                                  transparent 55%,
                                  rgba(0,0,0,0.92) 100%);
      pointer-events: none;
  }}
  .bi-lw-caption {{
      position: absolute; left: 0; right: 0; bottom: 0;
      padding: 10px 12px 11px 12px; z-index: 1;
  }}
  .bi-lw-title {{
      font-size: 13px; font-weight: 700; line-height: 1.2;
      color: white; letter-spacing: -0.01em;
      display: -webkit-box; -webkit-line-clamp: 2;
      -webkit-box-orient: vertical; overflow: hidden;
  }}
  .bi-lw-meta {{
      font-size: 10px; color: rgba(255,255,255,0.75);
      margin-top: 3px; letter-spacing: 0.04em;
  }}
  .bi-lw-type-badge {{
      position: absolute; top: 8px; left: 8px; z-index: 2;
      font-size: 9px; font-weight: 700; letter-spacing: 0.08em;
      text-transform: uppercase;
      padding: 2px 6px; border-radius: 3px;
      background: rgba(0,0,0,0.65); color: white;
      backdrop-filter: blur(6px);
  }}

  /* Continue Watching rail */
  .bi-rail-label {{
      font-size: 11px; font-weight: 700;
      text-transform: uppercase; letter-spacing: 0.14em;
      color: {DIM};
      margin: 8px 0 10px 0;
      padding-left: 10px;
      border-left: 2px solid {NETFLIX_RED};
      display: flex; align-items: center; gap: 8px;
  }}
  a.bi-rail-link, a.bi-rail-link:visited, a.bi-rail-link:hover {{
      text-decoration: none !important; color: inherit !important;
      display: block; cursor: pointer;
  }}
  .bi-rail-card {{
      background: linear-gradient(135deg, var(--c1, #333) 0%, var(--c2, #111) 100%);
      border-radius: 10px;
      padding: 14px;
      min-height: 110px;
      display: flex; flex-direction: column; justify-content: space-between;
      border: 1px solid {BORDER};
      position: relative; overflow: hidden;
      transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1);
      animation: biFadeInUp 0.4s ease-out both;
  }}
  .bi-rail-card::after {{
      content: ""; position: absolute; inset: 0;
      background: linear-gradient(180deg, transparent 40%, rgba(0,0,0,0.65) 100%);
      pointer-events: none;
  }}
  .bi-rail-card:hover {{
      transform: translateY(-3px);
      border-color: {NETFLIX_RED};
      box-shadow: 0 10px 24px rgba(229, 9, 20, 0.2);
  }}
  .bi-rail-title {{
      font-size: 13px; font-weight: 700; color: white;
      line-height: 1.2; position: relative; z-index: 1;
  }}
  .bi-rail-progress {{
      font-size: 10px; color: rgba(255,255,255,0.85);
      margin-top: 2px; position: relative; z-index: 1;
  }}
  .bi-rail-bar {{
      height: 3px; border-radius: 2px;
      background: rgba(255,255,255,0.2);
      margin-top: 8px; overflow: hidden;
      position: relative; z-index: 1;
  }}
  .bi-rail-bar-fill {{
      height: 100%; background: {NETFLIX_RED};
      transition: width 0.3s ease;
  }}

  /* Audit: trend chart container */
  .bi-trend-wrap {{
      background: {CARD}; border: 1px solid {BORDER};
      border-radius: 12px; padding: 16px;
      margin-bottom: 20px;
  }}

  /* ========================================================================
     Title deep-dive screen
     ======================================================================== */
  a.bi-poster-link, a.bi-poster-link:visited, a.bi-poster-link:hover {{
      text-decoration: none !important; color: inherit !important;
      display: block; cursor: pointer;
  }}

  .bi-td-hero {{
      position: relative;
      border-radius: 16px;
      overflow: hidden;
      min-height: 360px;
      margin: 10px 0 24px 0;
      padding: 32px;
      border: 1px solid {BORDER};
      animation: biFadeInUp 0.5s ease-out both;
  }}
  .bi-td-hero-inner {{
      display: flex; gap: 28px; align-items: flex-start;
      position: relative; z-index: 1;
      max-width: 1100px;
  }}
  .bi-td-poster {{
      flex-shrink: 0;
      width: 200px; aspect-ratio: 2/3;
      border-radius: 12px;
      background-color: {BG_DEEP};
      box-shadow: 0 20px 50px rgba(0,0,0,0.6);
      border: 1px solid {BORDER};
  }}
  .bi-td-text {{ flex: 1; min-width: 0; }}
  .bi-td-title {{
      font-size: 44px; font-weight: 900; letter-spacing: -0.03em;
      line-height: 1.05; color: white; margin-bottom: 6px;
  }}
  .bi-td-meta {{
      font-size: 13px; color: {DIM}; margin-bottom: 12px;
      letter-spacing: 0.04em;
  }}
  .bi-td-tagline {{
      font-size: 16px; color: {DIM}; font-style: italic;
      margin: 8px 0 10px 0;
      border-left: 2px solid {NETFLIX_RED}; padding-left: 10px;
  }}
  .bi-td-director {{ font-size: 13px; color: {DIM}; margin-bottom: 10px; }}
  .bi-td-rating {{
      display: inline-block; font-size: 20px; font-weight: 800;
      color: #FFC107; margin: 6px 0;
  }}
  .bi-td-rating-sub {{
      font-size: 12px; color: {DIMMER}; font-weight: 500;
  }}

  .bi-td-synopsis {{
      color: {TEXT}; font-size: 14.5px; line-height: 1.6;
      max-width: 820px; margin-bottom: 24px;
  }}

  /* Cast carousel */
  .bi-td-cast-card {{
      text-align: center;
      animation: biFadeInUp 0.4s ease-out both;
  }}
  .bi-td-cast-photo {{
      width: 100%; aspect-ratio: 1/1;
      border-radius: 50%;
      background-size: cover; background-position: center;
      background-color: {BG_LIFT};
      border: 2px solid {BORDER};
      transition: all 0.2s ease;
      margin-bottom: 8px;
  }}
  .bi-td-cast-photo:hover {{
      transform: scale(1.05);
      border-color: {NETFLIX_RED};
      box-shadow: 0 6px 20px rgba(229, 9, 20, 0.3);
  }}
  .bi-td-cast-photo-blank {{
      background: linear-gradient(135deg, {CARD} 0%, {BG_LIFT} 100%);
  }}
  .bi-td-cast-name {{
      font-size: 12.5px; font-weight: 700; color: {TEXT};
      line-height: 1.2; margin-bottom: 2px;
  }}
  .bi-td-cast-role {{
      font-size: 11px; color: {DIMMER};
      line-height: 1.2; font-style: italic;
      display: -webkit-box; -webkit-line-clamp: 2;
      -webkit-box-orient: vertical; overflow: hidden;
  }}
</style>
"""


def inject_theme() -> None:
    """Call once near the top of a Streamlit script."""
    st.markdown(_CSS, unsafe_allow_html=True)


# Deterministic poster color pair from a title string
def poster_gradient(seed: str) -> tuple[str, str]:
    """Return (c1, c2) hex pair for a Netflix-ish poster gradient."""
    palettes = [
        ("#8E0E00", "#1F1C18"),   # red-black
        ("#141E30", "#243B55"),   # midnight blue
        ("#232526", "#414345"),   # steel
        ("#000428", "#004e92"),   # deep ocean
        ("#1a2a6c", "#b21f1f"),   # royal + crimson
        ("#200122", "#6f0000"),   # wine
        ("#434343", "#000000"),   # charcoal
        ("#0f2027", "#2c5364"),   # teal-dark
        ("#360033", "#0b8793"),   # purple-teal
        ("#3a1c71", "#d76d77"),   # purple-rose
        ("#1e3c72", "#2a5298"),   # ocean blue
        ("#232526", "#8e2de2"),   # charcoal-violet
    ]
    h = sum(ord(c) for c in seed) % len(palettes)
    return palettes[h]
