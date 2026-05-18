"""
Mesa Careers AI — Streamlit Dashboard

State-machine UI driven by st.session_state.view:
  input      → Landing: LinkedIn URL or PDF upload
  profiling  → S1 + S2 running with live status
  confirmed  → Candidate snapshot card + "Start Job Search"
  searching  → S3 + S4 running with live status
  results    → Ranked job cards (expandable)
  draft      → Split screen: job card left, email/DM right
  dossier    → Full-width dossier + PDF download
  history    → Past runs from Supabase
"""
import logging
import os
import streamlit as st
from datetime import datetime, timezone
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

# ── Page config — must be FIRST Streamlit call ────────────────────────────────
st.set_page_config(
    page_title="Mesa Careers AI",
    page_icon="🎓",
    layout="wide",
    initial_sidebar_state="collapsed",
)

log = logging.getLogger(__name__)

# ── CSS ───────────────────────────────────────────────────────────────────────
_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap');

/* ── Reset & Base ── */
*, *::before, *::after { box-sizing: border-box; }
html, body, .stApp {
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif !important;
  background: #ECF0F9 !important;
  color: #374151 !important;
}
.block-container {
  padding-top: 2rem !important;
  padding-bottom: 3rem !important;
  max-width: 1160px !important;
}
footer, #MainMenu, header { visibility: hidden; }
[data-testid="stSidebar"] { display: none; }
h1, h2, h3, h4, h5 {
  font-family: 'Inter', sans-serif !important;
  color: #0B1929 !important;
  letter-spacing: -0.02em;
}
/* ── Preserve white text in ALL dark-background sections ── */
.mesa-hero h1, .mesa-hero h2, .mesa-hero h3,
.mesa-hero p, .mesa-hero span, .mesa-hero div,
.mesa-header h1, .mesa-header h2, .mesa-header h3,
.mesa-header p, .mesa-header span, .mesa-header div,
.mesa-cta-card h1, .mesa-cta-card h2, .mesa-cta-card h3,
.mesa-cta-card p, .mesa-cta-card span, .mesa-cta-card div,
.input-method-card p, .input-method-card span, .input-method-card div {
  color: #FFFFFF !important;
}
/* Specific tint overrides */
.mesa-hero .hero-title span  { color: #FF6B35 !important; }
.mesa-hero .hero-pill         { color: #FFA07A !important; }
.mesa-hero .hero-stat-lbl     { color: #FFFFFF !important; }
.mesa-header .mesa-sub-label  { color: rgba(255,255,255,0.5) !important; }
.mesa-header .mesa-sub-text   { color: rgba(255,255,255,0.65) !important; }
.mesa-cta-card .cta-sub       { color: rgba(255,255,255,0.65) !important; }
/* Streamlit markdown wrapper overrides for dark sections */
div[data-testid="stMarkdown"] .mesa-header p  { color: #FFFFFF !important; }
div[data-testid="stMarkdown"] .mesa-header h2 { color: #FFFFFF !important; }
div[data-testid="stMarkdown"] .mesa-header span { color: #FFFFFF !important; }
div[data-testid="stMarkdown"] .mesa-cta-card p  { color: #FFFFFF !important; }
div[data-testid="stMarkdown"] .mesa-cta-card span { color: #FFFFFF !important; }
div[data-testid="stMarkdown"] .mesa-hero p    { color: #FFFFFF !important; }
div[data-testid="stMarkdown"] .mesa-hero span { color: #FFFFFF !important; }
div[data-testid="stMarkdown"] .mesa-hero .hero-stat-val { color: #FFFFFF !important; }
div[data-testid="stMarkdown"] .mesa-hero .hero-stat-lbl { color: #FFFFFF !important; }

/* ── Hero Banner ── */
.mesa-hero {
  background: linear-gradient(135deg, #080F1E 0%, #0F2040 40%, #152D5C 70%, #0F2040 100%);
  border-radius: 24px;
  padding: 52px 52px 48px;
  margin-bottom: 36px;
  position: relative;
  overflow: hidden;
  box-shadow: 0 20px 60px rgba(8,15,30,0.35), 0 4px 16px rgba(8,15,30,0.2);
}
.mesa-hero::before {
  content: '';
  position: absolute; inset: 0;
  background:
    radial-gradient(ellipse 60% 80% at 5% 50%, rgba(232,82,26,0.18) 0%, transparent 60%),
    radial-gradient(ellipse 50% 60% at 95% 10%, rgba(30,58,110,0.6) 0%, transparent 55%),
    radial-gradient(ellipse 40% 50% at 50% 100%, rgba(232,82,26,0.08) 0%, transparent 50%);
  pointer-events: none;
}
.mesa-hero::after {
  content: '';
  position: absolute; inset: 0;
  background-image: radial-gradient(circle, rgba(255,255,255,0.06) 1px, transparent 1px);
  background-size: 28px 28px;
  pointer-events: none;
}
.hero-inner { position: relative; z-index: 2; }
.hero-pill {
  display: inline-flex; align-items: center; gap: 6px;
  background: rgba(232,82,26,0.2);
  border: 1px solid rgba(232,82,26,0.4);
  border-radius: 100px;
  padding: 5px 14px;
  font-size: 12px; font-weight: 600; letter-spacing: 0.5px;
  color: #FFA07A;
  margin-bottom: 18px;
  text-transform: uppercase;
}
.hero-title {
  font-size: 3rem; font-weight: 900; line-height: 1.1;
  color: #FFFFFF !important;
  margin: 0 0 14px 0;
  letter-spacing: -0.03em;
}
.hero-title span { color: #FF6B35 !important; }
.hero-sub {
  font-size: 1.1rem; font-weight: 400; line-height: 1.6;
  color: #FFFFFF !important;
  margin: 0; max-width: 540px;
}
.hero-stats {
  display: flex; gap: 32px; margin-top: 32px;
}
.hero-stat-val {
  font-size: 1.5rem; font-weight: 80; color: #FFFFFF !important;
  display: block; line-height: 1;
}
.hero-stat-lbl {

  font-size: 11px; font-weight: 500;
  color: #FFFFFF !important;
  text-transform: uppercase; letter-spacing: 0.6px;
  display: block; margin-top: 3px;
}

/* ── Page Headers (non-hero) ── */
.mesa-header {
  background: linear-gradient(135deg, #0B1929 0%, #152D5C 100%);
  border-radius: 20px;
  padding: 28px 36px;
  margin-bottom: 28px;
  box-shadow: 0 8px 32px rgba(11,25,41,0.2);
  position: relative; overflow: hidden;
}
.mesa-header::after {
  content: '';
  position: absolute; inset: 0;
  background-image: radial-gradient(circle, rgba(255,255,255,0.04) 1px, transparent 1px);
  background-size: 24px 24px;
  pointer-events: none;
}

/* ── Cards ── */
.mesa-card {
  background: #FFFFFF;
  border-radius: 18px;
  padding: 28px;
  box-shadow: 0 2px 12px rgba(11,25,41,0.06), 0 0 0 1px rgba(11,25,41,0.05);
  margin-bottom: 18px;
  transition: box-shadow 0.25s ease, transform 0.25s ease;
}
.mesa-card:hover {
  box-shadow: 0 8px 32px rgba(11,25,41,0.12), 0 0 0 1px rgba(232,82,26,0.15);
  transform: translateY(-2px);
}
.mesa-card-flat {
  background: #FFFFFF;
  border-radius: 18px;
  padding: 28px;
  box-shadow: 0 2px 12px rgba(11,25,41,0.06), 0 0 0 1px rgba(11,25,41,0.05);
  margin-bottom: 18px;
}

/* ── Input Method Cards ── */
.input-method-card {
  background: linear-gradient(135deg, #080F1E 0%, #0F2040 45%, #1A3565 100%);
  border-radius: 20px;
  padding: 28px 24px 24px;
  box-shadow: 0 8px 32px rgba(8,15,30,0.28), 0 0 0 1px rgba(255,255,255,0.06);
  min-height: 200px;
  transition: box-shadow 0.25s, transform 0.25s;
  position: relative; overflow: hidden;
  cursor: pointer;
}
.input-method-card::before {
  content: '';
  position: absolute; top: 0; left: 0; right: 0; height: 3px;
  background: linear-gradient(90deg, #E8521A, #FF8C5A);
  opacity: 1;
}
.input-method-card::after {
  content: '';
  position: absolute; inset: 0;
  background-image: radial-gradient(circle, rgba(255,255,255,0.05) 1px, transparent 1px);
  background-size: 24px 24px;
  pointer-events: none;
}
.input-method-card:hover {
  box-shadow: 0 14px 48px rgba(232,82,26,0.22), 0 0 0 1px rgba(232,82,26,0.4);
  transform: translateY(-4px);
}
.input-icon-wrap {
  width: 52px; height: 52px; border-radius: 15px;
  background: linear-gradient(135deg, rgba(232,82,26,0.25), rgba(232,82,26,0.12));
  border: 1px solid rgba(232,82,26,0.35);
  display: flex; align-items: center; justify-content: center;
  font-size: 24px; margin-bottom: 16px;
  position: relative; z-index: 1;
}
.input-card-title {
  font-size: 1.3rem; font-weight: 700; color: #FFFFFF;
  margin: 0 0 8px; position: relative; z-index: 1;
}
.input-card-sub {
  font-size: 14px; color: rgba(255,255,255,0.60);
  margin: 0 0 16px; line-height: 1.65; position: relative; z-index: 1;
}

/* ── Score display ── */
.score-ring-wrap {
  display: flex; align-items: center; gap: 14px; margin: 8px 0;
}
.score-number {
  font-size: 2rem; font-weight: 900; line-height: 1;
  letter-spacing: -0.03em;
}
.score-number.high { color: #10B981; }
.score-number.mid  { color: #F59E0B; }
.score-number.low  { color: #EF4444; }
.score-bar-wrap {
  flex: 1; background: #EEF2F7; border-radius: 100px; height: 10px; overflow: hidden;
}
.score-bar-fill { height: 100%; border-radius: 100px; transition: width 0.6s ease; }
.score-high { background: linear-gradient(90deg, #059669, #10B981, #34D399); }
.score-mid  { background: linear-gradient(90deg, #D97706, #F59E0B, #FCD34D); }
.score-low  { background: linear-gradient(90deg, #DC2626, #EF4444, #F87171); }

/* ── Axis score bars ── */
.axis-row  { display:flex; align-items:center; gap:10px; margin:6px 0; }
.axis-lbl  { font-size:12px; font-weight:500; color:#6B7A99; width:110px; flex-shrink:0; }
.axis-bar  { flex:1; background:#EEF2F7; border-radius:100px; height:8px; overflow:hidden; }
.axis-fill { height:100%; border-radius:100px;
             background: linear-gradient(90deg, #E8521A, #FF8C5A); }
.axis-val  { font-size:12px; font-weight:700; color:#0B1929; width:30px; text-align:right; }

/* ── Badges ── */
.badge {
  display: inline-flex; align-items: center;
  padding: 3px 10px; border-radius: 100px;
  font-size: 11px; font-weight: 600; letter-spacing: 0.2px;
  margin-right: 5px; white-space: nowrap;
}
.badge-direct  { background:#EFF6FF; color:#2563EB; border:1px solid #BFDBFE; }
.badge-signal  { background:#FFF7ED; color:#C2410C; border:1px solid #FED7AA; }
.badge-stage   { background:#ECFDF5; color:#059669; border:1px solid #A7F3D0; }
.badge-warning { background:#FEF2F2; color:#DC2626; border:1px solid #FECACA; }
.badge-neutral { background:#F8FAFC; color:#5A6478; border:1px solid #E2E8F0; }
.badge-pro     { background: linear-gradient(90deg,#E8521A,#FF6B35);
                 color: white; border: none; font-size: 10px; letter-spacing: 0.4px; }

/* ── Role tags ── */
.role-tag {
  display: inline-flex; align-items: center; gap: 6px;
  background: linear-gradient(135deg, #EEF2FF, #E0E7FF);
  color: #4338CA;
  border: 1px solid #C7D2FE;
  padding: 5px 14px; border-radius: 100px;
  font-size: 13px; font-weight: 600; margin: 3px 5px 3px 0;
}
.role-confidence {
  font-size: 9px; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.5px; opacity: 0.6;
}
/* ── Role alias chips ── */
.role-block { margin-bottom: 12px; }
.role-aliases-row {
  margin-top: 5px; padding-left: 2px;
  display: flex; flex-wrap: wrap; align-items: center; gap: 4px;
}
.alias-label {
  font-size: 11px; color: #94A3B8; font-weight: 500;
  margin-right: 2px; white-space: nowrap;
}
.alias-chip {
  display: inline-flex; align-items: center;
  background: #F8FAFC; color: #475569;
  border: 1px solid #E2E8F0; border-radius: 100px;
  padding: 2px 10px; font-size: 11px; font-weight: 500;
}

/* ── CTA card (dark, right column on confirmed view) ── */
.mesa-cta-card {
  background: linear-gradient(135deg, #080F1E 0%, #0F2040 50%, #1A3565 100%);
  border-radius: 20px;
  padding: 32px 28px 28px;
  box-shadow: 0 8px 32px rgba(8,15,30,0.3), 0 0 0 1px rgba(255,255,255,0.06);
  position: relative; overflow: hidden;
  margin-bottom: 14px;
}
.mesa-cta-card::after {
  content: '';
  position: absolute; inset: 0;
  background-image: radial-gradient(circle, rgba(255,255,255,0.05) 1px, transparent 1px);
  background-size: 24px 24px;
  pointer-events: none;
}
.mesa-cta-card::before {
  content: '';
  position: absolute; top: 0; left: 0; right: 0; height: 3px;
  background: linear-gradient(90deg, #E8521A, #FF8C5A);
}
.cta-icon-wrap {
  width: 56px; height: 56px; border-radius: 16px;
  background: linear-gradient(135deg, rgba(232,82,26,0.3), rgba(232,82,26,0.15));
  border: 1px solid rgba(232,82,26,0.4);
  display: flex; align-items: center; justify-content: center;
  font-size: 26px; margin: 0 auto 18px;
  position: relative; z-index: 1;
}
.cta-title {
  font-size: 1.25rem; font-weight: 800; color: #FFFFFF !important;
  margin: 0 0 10px; text-align: center;
  position: relative; z-index: 1;
}
.cta-sub {
  font-size: 13px; color: rgba(255,255,255,0.65) !important;
  line-height: 1.65; text-align: center; margin: 0;
  position: relative; z-index: 1;
}

/* ── Summary card (left column on confirmed view) ── */
.mesa-summary-card {
  background: #FFFFFF;
  border-radius: 18px;
  padding: 28px;
  box-shadow: 0 2px 12px rgba(11,25,41,0.06), 0 0 0 1px rgba(11,25,41,0.05);
  margin-bottom: 14px;
}

/* ── Draft panel ── */
.draft-panel {
  background: #FFFFFF; border-radius: 18px; padding: 28px;
  box-shadow: 0 2px 12px rgba(11,25,41,0.06), 0 0 0 1px rgba(11,25,41,0.05);
  border-top: 3px solid #E8521A;
  min-height: 450px;
}

/* ── Stat chips ── */
.stat-chip {
  display: inline-flex; align-items: center; gap: 6px;
  background: #F8FAFC; border: 1px solid #E2E8F0;
  border-radius: 10px; padding: 6px 12px;
  font-size: 12px; color: #4A5568; font-weight: 500;
  margin: 3px 4px 3px 0;
}

/* ── Divider ── */
.mesa-div { height: 1px; background: linear-gradient(90deg, transparent, #E2E8F0, transparent); margin: 20px 0; }

/* ── Section label ── */
.section-label {
  font-size: 15px; font-weight: 700; color: #E8521A;
  text-transform: uppercase; letter-spacing: 0.5px;
  margin-bottom: 14px; display: block;
}

/* ── Job card in results ── */
.job-card-rank {
  display: inline-flex; align-items: center; justify-content: center;
  width: 32px; height: 32px; border-radius: 10px;
  background: linear-gradient(135deg, #E8521A, #FF6B35);
  color: white; font-size: 13px; font-weight: 800;
  margin-right: 10px; flex-shrink: 0;
}

/* ── Animations ── */
@keyframes fadeInDown {
  from { opacity: 0; transform: translateY(-20px); }
  to   { opacity: 1; transform: translateY(0); }
}
@keyframes fadeInUp {
  from { opacity: 0; transform: translateY(20px); }
  to   { opacity: 1; transform: translateY(0); }
}
@keyframes pulse-glow {
  0%, 100% { box-shadow: 0 0 0 0 rgba(232,82,26,0); }
  50%       { box-shadow: 0 0 0 6px rgba(232,82,26,0.15); }
}
.anim-down { animation: fadeInDown 0.5s ease-out; }
.anim-up   { animation: fadeInUp   0.5s ease-out; }

/* ── Buttons ── */
div.stButton > button {
  font-family: 'Inter', sans-serif !important;
  font-weight: 600 !important; border-radius: 12px !important;
  transition: all 0.2s ease !important;
}
div.stButton > button[kind="primary"] {
  background: linear-gradient(135deg, #E8521A 0%, #C94315 100%) !important;
  color: white !important; border: none !important;
  box-shadow: 0 4px 16px rgba(232,82,26,0.35) !important;
  font-size: 15px !important; padding: 0.65rem 1.4rem !important;
}
div.stButton > button[kind="primary"]:hover {
  background: linear-gradient(135deg, #FF6B35 0%, #E8521A 100%) !important;
  box-shadow: 0 6px 24px rgba(232,82,26,0.45) !important;
  transform: translateY(-1px) !important;
}
div.stButton > button[kind="secondary"] {
  background: #FFFFFF !important; color: #0B1929 !important;
  border: 1.5px solid #E2E8F0 !important;
  box-shadow: 0 2px 8px rgba(11,25,41,0.06) !important;
}
div.stButton > button[kind="secondary"]:hover {
  border-color: #E8521A !important; color: #E8521A !important;
  box-shadow: 0 4px 16px rgba(232,82,26,0.12) !important;
}

/* ── Expander ── */
details > summary {
  font-weight: 700 !important; font-size: 15px !important;
  color: #0B1929 !important; padding: 12px 4px !important;
  font-family: 'Inter', sans-serif !important; letter-spacing: -0.01em;
}
details[open] > summary {
  color: #E8521A !important;
  font-size: 17px !important;
  font-weight: 800 !important;
  letter-spacing: -0.02em;
}

/* ── Text inputs ── */
div[data-testid="stTextInput"] > label,
div[data-testid="stTextInput"] label {
  font-size: 17px !important; font-weight: 700 !important;
  color: #0B1929 !important; font-family: 'Inter', sans-serif !important;
  margin-bottom: 8px !important; letter-spacing: -0.01em !important;
}
div[data-testid="stTextInput"] > div > div > input {
  border-radius: 12px !important; border: 1.5px solid #E2E8F0 !important;
  font-family: 'Inter', sans-serif !important; font-size: 15px !important;
  color: #0B1929 !important; padding: 0.65rem 1rem !important;
  background: #FAFBFC !important;
  caret-color: #E8521A !important;
  transition: border-color 0.2s, box-shadow 0.2s !important;
}
div[data-testid="stTextInput"] > div > div > input:focus {
  border-color: #E8521A !important;
  box-shadow: 0 0 0 3px rgba(232,82,26,0.12) !important;
}

/* ── File uploader ── */
div[data-testid="stFileUploader"] > label,
div[data-testid="stFileUploader"] label {
  font-size: 17px !important; font-weight: 700 !important;
  color: #0B1929 !important; font-family: 'Inter', sans-serif !important;
  margin-bottom: 8px !important;
}
div[data-testid="stFileUploader"] > div {
  border-radius: 14px !important; border: 2px dashed #CBD5E1 !important;
  background: #FAFBFC !important;
  transition: border-color 0.2s, background 0.2s !important;
}
div[data-testid="stFileUploader"] > div:hover {
  border-color: #E8521A !important; background: #FFF8F5 !important;
}
/* File selected state — filename, size, delete btn all visible */
div[data-testid="stFileUploader"] span,
div[data-testid="stFileUploader"] p,
div[data-testid="stFileUploader"] div:not([data-testid]) {
  color: #0B1929 !important;
}
div[data-testid="stFileUploader"] small {
  color: #6B7A99 !important; font-size: 12px !important;
}
div[data-testid="stFileUploaderFileName"] {
  color: #0B1929 !important; font-weight: 600 !important; font-size: 14px !important;
}
div[data-testid="stFileUploaderDeleteBtn"] button {
  color: #6B7A99 !important;
}
div[data-testid="stFileUploaderDeleteBtn"] button:hover {
  color: #EF4444 !important;
}

/* ── Text area (draft view) ── */
div[data-testid="stTextArea"] > label {
  color: #0B1929 !important; font-weight: 600 !important;
  font-size: 14px !important; font-family: 'Inter', sans-serif !important;
}
div[data-testid="stTextArea"] textarea {
  color: #0B1929 !important; background: #FAFBFC !important;
  caret-color: #E8521A !important;
  border: 1.5px solid #E2E8F0 !important; border-radius: 12px !important;
  font-family: 'Inter', sans-serif !important; font-size: 14px !important;
}
div[data-testid="stTextArea"] textarea:focus {
  border-color: #E8521A !important;
  box-shadow: 0 0 0 3px rgba(232,82,26,0.12) !important;
}

/* ── Code blocks (st.code) ── */
div[data-testid="stCode"] pre,
div[data-testid="stCode"] code {
  color: #0B1929 !important; background: #F1F5F9 !important;
  border-radius: 10px !important;
}

/* ── Status widget ── */
div[data-testid="stStatusWidget"] {
  border-radius: 14px !important; border: 1px solid #E2E8F0 !important;
}
div[data-testid="stStatusWidget"] p,
div[data-testid="stStatusWidget"] span,
div[data-testid="stStatusWidget"] div { color: #374151 !important; }

/* ── Expander content ── */
details > div p,
details > div span,
details > div li { color: #374151 !important; }

/* ── Info / warning / error / success boxes ── */
div[data-testid="stAlert"] {
  border-radius: 12px !important; font-family: 'Inter', sans-serif !important;
}
div[data-testid="stAlert"] p,
div[data-testid="stAlert"] span { color: inherit !important; }

/* ── Spinner text ── */
div[data-testid="stSpinner"] p,
div[data-testid="stSpinner"] span { color: #374151 !important; }

/* ── Markdown rendered text ── */
div[data-testid="stMarkdown"] p,
div[data-testid="stMarkdown"] li,
div[data-testid="stMarkdown"] span { color: #374151 !important; }
div[data-testid="stMarkdown"] a { color: #E8521A !important; }
div[data-testid="stMarkdown"] strong { color: #0B1929 !important; }

/* ── Scrollbar ── */
::-webkit-scrollbar { width: 6px; }
::-webkit-scrollbar-track { background: #F1F5F9; }
::-webkit-scrollbar-thumb { background: #CBD5E1; border-radius: 100px; }
::-webkit-scrollbar-thumb:hover { background: #94A3B8; }
</style>
"""

# ── Session state defaults ────────────────────────────────────────────────────
_DEFAULTS: dict = {
    "view":               "input",
    "candidate_id":       None,
    "student_profile":    None,
    "candidate_model":    None,
    "scored_leads":       [],
    "selected_match_id":  None,
    "draft_type":         "email",
    "outreach_draft":     None,
    "dossier_output":     None,
    "error_msg":          None,
    "_pending_url":       "",
    "_pending_pdf":       None,
    "_pending_name":      "",
    "_hist_name":         "Candidate",
}


def _init():
    for k, v in _DEFAULTS.items():
        if k not in st.session_state:
            st.session_state[k] = v
    # On fresh session, try to restore state from URL query params (page refresh)
    if st.session_state.view == "input" and not st.session_state.candidate_id:
        _restore_from_query_params()


def _goto(view: str):
    st.session_state.view = view
    _sync_query_params()
    st.rerun()


def _reset():
    for k, v in _DEFAULTS.items():
        st.session_state[k] = v
    st.query_params.clear()
    st.rerun()


def _sync_query_params():
    """Write current session state into URL query params so refresh restores it."""
    params: dict = {}
    view = st.session_state.get("view", "input")
    cid  = st.session_state.get("candidate_id")
    mid  = st.session_state.get("selected_match_id")
    dt   = st.session_state.get("draft_type", "email")

    if view and view != "input":
        params["v"] = view
    if cid:
        params["cid"] = cid
    if mid:
        params["mid"] = mid
    if dt and dt != "email":
        params["dt"] = dt

    try:
        st.query_params.from_dict(params)
    except Exception:
        pass


def _restore_from_query_params():
    """On page refresh, read URL params and rebuild session state from Supabase."""
    try:
        params = st.query_params
        view   = params.get("v", "input")
        cid    = params.get("cid")

        if not cid or view == "input":
            return

        from db.queries import get_candidate, get_matches_for_candidate
        candidate = get_candidate(cid)
        if not candidate:
            return

        st.session_state.candidate_id = cid
        st.session_state._hist_name   = candidate.get("name", "Candidate")

        # Restore scored leads for views that need them
        if view in ("results", "draft", "dossier", "searching"):
            try:
                matches = get_matches_for_candidate(cid)
                for m in matches:
                    if "job_id" not in m:
                        m["job_id"] = m.get("id")
                st.session_state.scored_leads = matches
                # If no scored leads yet, fall back to results view
                if not matches and view in ("draft", "dossier"):
                    view = "results"
            except Exception as e:
                log.warning("Could not restore scored leads: %s", e)

        # Restore selected_match_id for draft / dossier
        mid = params.get("mid")
        if mid:
            st.session_state.selected_match_id = mid
        st.session_state.draft_type = params.get("dt", "email")

        # Restore profile + candidate_model for confirmed view
        if view == "confirmed":
            profile_data = candidate.get("student_profile")
            if profile_data:
                try:
                    from models.student import StudentProfile
                    st.session_state.student_profile = StudentProfile.model_validate(profile_data)
                except Exception as e:
                    log.warning("Could not restore StudentProfile: %s", e)
                    view = "results"

            model = _build_candidate_model_from_db(candidate)
            if model:
                st.session_state.candidate_model = model
            else:
                view = "results"

        st.session_state.view = view
        log.info("Session restored from URL params: view=%s cid=%s", view, cid)

    except Exception as e:
        log.warning("Session restore failed: %s — starting fresh", e)


def _build_candidate_model_from_db(candidate: dict):
    """Reconstruct a CandidateModel from a Supabase candidate record."""
    try:
        from models.student import (
            CandidateModel, TargetRole, ConfidenceLevel,
            CompensationBand, SearchQueries,
        )

        target_roles = []
        for r in (candidate.get("target_roles") or []):
            if isinstance(r, dict):
                try:
                    conf = ConfidenceLevel(r.get("confidence", "medium"))
                except ValueError:
                    conf = ConfidenceLevel.medium
                target_roles.append(TargetRole(
                    title      = r.get("title", ""),
                    confidence = conf,
                    aliases    = r.get("aliases", []),
                ))

        # Parse "30L - 45L" → CompensationBand
        comp_band = None
        comp_str  = (candidate.get("compensation_band_inr") or "").replace(" ", "")
        if comp_str:
            try:
                parts = comp_str.replace("L", "").split("-")
                if len(parts) == 2:
                    comp_band = CompensationBand(
                        low_lpa  = int(float(parts[0])),
                        high_lpa = int(float(parts[1])),
                    )
            except Exception:
                pass

        sq_raw = candidate.get("search_queries") or {}
        search_queries = SearchQueries(
            formal_platforms = sq_raw.get("formal_platforms", []),
            hidden_signals   = sq_raw.get("hidden_signals",   []),
        )

        return CandidateModel(
            target_roles      = target_roles,
            sector_fit        = candidate.get("sector_fit")   or [],
            compensation_band = comp_band,
            dealbreakers      = candidate.get("dealbreakers") or [],
            x_factor          = candidate.get("x_factor")     or "",
            search_queries    = search_queries,
        )
    except Exception as e:
        log.warning("Could not reconstruct CandidateModel from DB: %s", e)
        return None


def _display_name() -> str:
    p = st.session_state.student_profile
    return p.name if p else st.session_state.get("_hist_name", "Candidate")


# ── UI helpers ────────────────────────────────────────────────────────────────

def _score_cls(s: float) -> str:
    if s >= 75: return "high"
    if s >= 55: return "mid"
    return "low"


def _score_bar(s: float, height: int = 10) -> str:
    cls = _score_cls(s)
    pct = min(s, 100)
    return (
        f'<div class="score-bar-wrap" style="height:{height}px;">'
        f'<div class="score-bar-fill score-{cls}" style="width:{pct:.0f}%;height:{height}px;"></div>'
        f'</div>'
    )


def _score_display(s: float) -> str:
    cls = _score_cls(s)
    return (
        f'<div class="score-ring-wrap">'
        f'<span class="score-number {cls}">{s:.0f}</span>'
        f'<div style="flex:1;">'
        f'<div style="font-size:10px;color:#6B7A99;font-weight:600;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px;">Fit Score</div>'
        f'{_score_bar(s)}'
        f'</div>'
        f'</div>'
    )


def _badge(text: str, kind: str = "neutral") -> str:
    return f'<span class="badge badge-{kind}">{text}</span>'


def _source_badge(src: str) -> str:
    if any(k in src.lower() for k in ("hidden", "indirect", "post", "signal")):
        return _badge("⚡ Founder Signal", "signal")
    return _badge("💼 LinkedIn Jobs", "direct")


def _stage_badge(lbl: Optional[str]) -> str:
    if not lbl or lbl == "unknown":
        return ""
    return _badge(lbl.replace("_", " ").title(), "stage")


def _axis_bars(scores: dict) -> str:
    axes = [
        ("Skills",        "skills"),
        ("Experience",    "experience"),
        ("Domain",        "domain"),
        ("Company Stage", "company_stage"),
    ]
    rows = []
    for lbl, key in axes:
        val = float(scores.get(key, 5.0))
        rows.append(
            f'<div class="axis-row">'
            f'<span class="axis-lbl">{lbl}</span>'
            f'<div class="axis-bar">'
            f'<div class="axis-fill" style="width:{val*10:.0f}%"></div>'
            f'</div>'
            f'<span class="axis-val">{val:.1f}</span>'
            f'</div>'
        )
    return "\n".join(rows)


def _rel_date(val) -> str:
    if not val:
        return ""
    try:
        if isinstance(val, str):
            dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
        else:
            dt = val
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        days = (datetime.now(timezone.utc) - dt).days
        if days == 0:   return "Today"
        if days == 1:   return "Yesterday"
        if days < 7:    return f"{days}d ago"
        if days < 30:   return f"{days//7}w ago"
        if days < 365:  return f"{days//30}mo ago"
        return f"{days//365}y ago"
    except Exception:
        return ""


def _lead_score(lead: dict) -> float:
    return float(lead.get("fit_score") or lead.get("final_score") or 0)


# ── Pipeline runners ──────────────────────────────────────────────────────────

def _run_profiling():
    from pipeline.s1_ingest import ingest_profile
    from pipeline.s2_synthesise import synthesise_candidate

    url  = st.session_state._pending_url
    pdf  = st.session_state._pending_pdf
    name = st.session_state._pending_name

    try:
        with st.status("Analysing your profile…", expanded=True) as status:
            st.write("**Stage 1** — Extracting and normalising profile data…")
            profile, cid = ingest_profile(
                linkedin_url = url or None,
                pdf_bytes    = pdf or None,
                student_name = name,
            )
            st.write(f"✅ Profile ready — **{profile.name}**, {profile.seniority.value}")
            st.write("**Stage 2** — Synthesising your market positioning with Gemini Pro…")
            model = synthesise_candidate(cid, profile)
            st.write(f"✅ {len(model.target_roles)} target roles identified")
            status.update(label="Profile analysed!", state="complete", expanded=False)

        st.session_state.candidate_id    = cid
        st.session_state.student_profile = profile
        st.session_state.candidate_model = model
        st.session_state.error_msg       = None
        _goto("confirmed")

    except Exception as exc:
        log.exception("S1/S2 failed")
        st.session_state.error_msg = str(exc)
        _goto("input")


def _run_searching():
    from pipeline.s3_discover import discover_leads
    from pipeline.s4_rank    import rank_leads

    cid   = st.session_state.candidate_id
    model = st.session_state.candidate_model

    if not model:
        st.error("Candidate model missing. Please start over.")
        if st.button("Start over"):
            _reset()
        return

    try:
        with st.status("Searching for your opportunities…", expanded=True) as status:
            st.write("**Stage 3** — Searching LinkedIn Jobs, Wellfound & founder posts…")
            direct, indirect = discover_leads(cid, model.search_queries.model_dump())
            st.write(
                f"✅ Found **{len(direct)}** formal listings + "
                f"**{len(indirect)}** founder signals"
            )
            st.write("**Stage 4** — Scoring and ranking all leads with AI…")
            scored = rank_leads(cid, model)
            st.write(
                f"✅ **{len(scored)}** leads ranked — top fit score: {scored[0].fit_score:.1f}/100"
                if scored else "✅ Ranking complete"
            )
            status.update(label="Ranking complete!", state="complete", expanded=False)

        st.session_state.scored_leads = [
            {**lead.model_dump(), "job_id": lead.job_id}
            for lead in scored
        ]
        st.session_state.error_msg = None
        _goto("results")

    except Exception as exc:
        log.exception("S3/S4 failed")
        st.session_state.error_msg = str(exc)
        _goto("confirmed")


# ── VIEW: input ───────────────────────────────────────────────────────────────

def _view_input():
    # Hero
    st.markdown("""
    <div class="mesa-hero anim-down">
      <div class="hero-inner">
        <div class="hero-pill">✦ Powered by Gemini 2.5 Pro</div>
        <h1 class="hero-title">Mesa <span>Careers</span> AI</h1>
        <p class="hero-sub">
          Drop a LinkedIn URL or resume PDF. Our AI finds ranked job leads,
          drafts personalised outreach, and builds a placement dossier — in minutes.
        </p>
        <div class="hero-stats">
          <div>
            <span class="hero-stat-val">6</span>
            <span class="hero-stat-lbl">AI Pipeline Stages</span>
          </div>
          <div>
            <span class="hero-stat-val">20+</span>
            <span class="hero-stat-lbl">Ranked Leads</span>
          </div>
          <div>
            <span class="hero-stat-val">3</span>
            <span class="hero-stat-lbl">Job Channels</span>
          </div>
        </div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    if st.session_state.error_msg:
        st.error(f"⚠ {st.session_state.error_msg}")
        st.session_state.error_msg = None

    st.markdown(
        '<span class="section-label">Choose your input method</span>',
        unsafe_allow_html=True,
    )

    col_url, col_pdf = st.columns(2, gap="large")

    with col_url:
        st.markdown("""
        <div class="input-method-card">
          <div class="input-icon-wrap">🔗</div>
          <div class="input-card-title">LinkedIn Profile URL</div>
          <div class="input-card-sub">Paste your LinkedIn URL and we'll scrape your full profile automatically via Apify.</div>
        </div>
        """, unsafe_allow_html=True)
        linkedin_url = st.text_input(
            "LinkedIn URL",
            placeholder="https://www.linkedin.com/in/your-name",
            label_visibility="collapsed",
            key="form_url",
        )

    with col_pdf:
        st.markdown("""
        <div class="input-method-card">
          <div class="input-icon-wrap">📄</div>
          <div class="input-card-title">Resume PDF</div>
          <div class="input-card-sub">Upload any resume PDF — not just LinkedIn exports. Gemini will normalise the data.</div>
        </div>
        """, unsafe_allow_html=True)
        uploaded = st.file_uploader(
            "Upload PDF",
            type=["pdf"],
            label_visibility="collapsed",
            key="form_pdf",
        )

    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
    student_name = st.text_input(
        "Your name",
        placeholder="e.g. Priya Sharma  (optional — helps if unclear in the document)",
        key="form_name",
    )

    st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
    col_btn, col_hist = st.columns([3, 1], gap="medium")
    with col_btn:
        if st.button("✨  Analyse My Profile", type="primary", use_container_width=True):
            url = (linkedin_url or "").strip()
            pdf = uploaded.read() if uploaded else None
            if not url and not pdf:
                st.warning("Please enter a LinkedIn URL or upload a PDF to continue.")
            else:
                st.session_state._pending_url  = url
                st.session_state._pending_pdf  = pdf
                st.session_state._pending_name = student_name.strip()
                _goto("profiling")
    with col_hist:
        if st.button("🕒  Past Runs", use_container_width=True):
            _goto("history")


# ── VIEW: profiling ───────────────────────────────────────────────────────────

def _view_profiling():
    st.markdown(
        '<div class="mesa-header anim-down">'
        '<h2 style="color:white;margin:0;font-size:1.5rem;">🔍 Analysing your profile…</h2>'
        '<p style="color:rgba(255,255,255,0.55);margin:4px 0 0;font-size:13px;">Stage 1 → Profile ingestion &nbsp;·&nbsp; Stage 2 → AI synthesis with Gemini Pro</p>'
        '</div>',
        unsafe_allow_html=True,
    )
    _run_profiling()


# ── VIEW: confirmed ───────────────────────────────────────────────────────────

def _view_confirmed():
    profile = st.session_state.student_profile
    model   = st.session_state.candidate_model

    # ── Header — all inline styles use explicit white ─────────────────────────
    st.markdown(
        f'<div class="mesa-header anim-down">'
        f'<p class="mesa-sub-label" style="margin:0 0 6px;font-size:11px;font-weight:700;'
        f'text-transform:uppercase;letter-spacing:0.8px;">Profile Analysed</p>'
        f'<h2 style="color:#FFFFFF !important;margin:0 0 5px;font-size:1.7rem;font-weight:800;">'
        f'👋 Hey, {profile.name}!</h2>'
        f'<p class="mesa-sub-text" style="font-size:13px;margin:0;line-height:1.5;">'
        f'{profile.headline}</p>'
        f'</div>',
        unsafe_allow_html=True,
    )

    col_snap, col_cta = st.columns([3, 2], gap="large")

    # ── Left: AI Summary card — ALL content in ONE st.markdown call ───────────
    with col_snap:
        # Build roles HTML
        roles_html = ""
        if model.target_roles:
            for r in model.target_roles:
                alias_html = ""
                if r.aliases:
                    chips = "".join(
                        f'<span class="alias-chip">{a}</span>'
                        for a in r.aliases
                    )
                    alias_html = (
                        f'<div class="role-aliases-row">'
                        f'<span class="alias-label">also searched as:</span>{chips}'
                        f'</div>'
                    )
                roles_html += (
                    f'<div class="role-block">'
                    f'<span class="role-tag">{r.title}&nbsp;'
                    f'<span class="role-confidence">{r.confidence.value}</span></span>'
                    f'{alias_html}</div>'
                )
            roles_html = (
                f'<div style="margin-bottom:4px;">'
                f'<div style="font-size:13px;font-weight:700;color:#0B1929;margin-bottom:8px;">Target Roles</div>'
                f'{roles_html}'
                f'</div>'
            )

        # Build stat chips HTML
        chips_html = ""
        if model.compensation_band:
            chips_html = (
                f'<div style="margin:12px 0 4px;display:flex;flex-wrap:wrap;gap:6px;">'
                f'<span class="stat-chip">💰 {model.compensation_band.low_lpa}L – {model.compensation_band.high_lpa}L INR</span>'
                f'<span class="stat-chip">📍 {profile.location}</span>'
                f'<span class="stat-chip">🎯 {profile.seniority.value.title()}</span>'
                f'</div>'
            )

        # Build sector HTML
        sector_html = ""
        if model.sector_fit:
            sector_html = (
                f'<div style="margin:12px 0;font-size:13px;color:#374151;">'
                f'<strong style="color:#0B1929;">Sector Fit:</strong> {", ".join(model.sector_fit)}'
                f'</div>'
            )

        # Build x-factor HTML
        xfactor_html = ""
        if model.x_factor:
            xfactor_html = (
                f'<div style="background:linear-gradient(135deg,#FFF8F5,#FFF0EB);'
                f'border:1px solid #FED7B5;border-left:3px solid #E8521A;'
                f'border-radius:12px;padding:14px 16px;margin:14px 0;">'
                f'<div style="font-size:10px;font-weight:700;color:#E8521A;'
                f'text-transform:uppercase;letter-spacing:0.6px;margin-bottom:5px;">✦ X-Factor</div>'
                f'<div style="font-size:13px;color:#0B1929;line-height:1.6;">{model.x_factor}</div>'
                f'</div>'
            )

        # Build dealbreakers HTML
        db_html = ""
        if model.dealbreakers:
            chips = " ".join(
                f'<span class="badge badge-warning">✕ {d}</span>'
                for d in model.dealbreakers
            )
            db_html = (
                f'<div style="margin-top:10px;font-size:13px;">'
                f'<strong style="color:#0B1929;">Dealbreakers:</strong> {chips}'
                f'</div>'
            )

        # Render entire card in ONE call — no blank box issue
        st.markdown(
            f'<div class="mesa-summary-card anim-up">'
            f'<span class="section-label">AI-Identified Summary</span>'
            f'{roles_html}{chips_html}{sector_html}{xfactor_html}{db_html}'
            f'</div>',
            unsafe_allow_html=True,
        )

    # ── Right: dark CTA card ──────────────────────────────────────────────────
    with col_cta:
        # Dark card header — ONE st.markdown call
        st.markdown(
            '<div class="mesa-cta-card anim-up">'
            '<div class="cta-icon-wrap" style="position:relative;z-index:1;">🚀</div>'
            '<div class="cta-title">Ready to find your leads?</div>'
            '<p class="cta-sub">'
            'We\'ll search LinkedIn Jobs, Wellfound, and founder posts, '
            'then score and rank every lead by AI fit score.'
            '</p>'
            '</div>',
            unsafe_allow_html=True,
        )

        if st.session_state.error_msg:
            st.error(st.session_state.error_msg)
            st.session_state.error_msg = None

        if st.button("🚀  Start Job Search", type="primary", use_container_width=True):
            _goto("searching")

        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

        if st.button("← Start over", use_container_width=True):
            _reset()


# ── VIEW: searching ───────────────────────────────────────────────────────────

def _view_searching():
    st.markdown(
        '<div class="mesa-header anim-down">'
        '<h2 style="color:white;margin:0;font-size:1.5rem;">🌐 Searching for opportunities…</h2>'
        '<p style="color:rgba(255,255,255,0.55);margin:4px 0 0;font-size:13px;">Stage 3 → Lead discovery &nbsp;·&nbsp; Stage 4 → AI scoring & ranking</p>'
        '</div>',
        unsafe_allow_html=True,
    )
    _run_searching()


# ── VIEW: results ─────────────────────────────────────────────────────────────

def _view_results():
    min_fit = int(os.getenv("MIN_FIT_SCORE", "55"))
    all_leads = st.session_state.scored_leads
    leads = [l for l in all_leads if _lead_score(l) >= min_fit]
    name  = _display_name()

    col_h, col_btns = st.columns([4, 2], gap="medium")
    with col_h:
        st.markdown(
            f'<h2 style="margin:0 0 6px;font-size:2rem;font-weight:800;color:#0B1929;">🏆 Ranked Leads</h2>'
            f'<p style="color:#374151;font-size:16px;font-weight:500;margin:0;">'
            f'{name} &nbsp;·&nbsp; <strong style="color:#E8521A;">{len(leads)}</strong> opportunities found</p>',
            unsafe_allow_html=True,
        )
    with col_btns:
        c1, c2 = st.columns(2)
        with c1:
            if st.button("+ New Analysis", type="primary", use_container_width=True):
                _reset()
        with c2:
            if st.button("🕒 History", use_container_width=True):
                _goto("history")

    st.markdown('<div class="mesa-div"></div>', unsafe_allow_html=True)

    if not leads:
        st.markdown(
            '<div style="background:#FEF2F2;border:1px solid #FECACA;border-left:4px solid #EF4444;'
            'border-radius:14px;padding:20px 24px;margin:16px 0;">'
            '<div style="font-size:16px;font-weight:700;color:#DC2626;margin-bottom:6px;">No ranked leads found</div>'
            '<div style="font-size:14px;color:#374151;line-height:1.6;">'
            'The pipeline may still be running, or no leads passed the minimum fit score threshold. '
            'Try running a new job search, or check that the Supabase tables have data.'
            '</div></div>',
            unsafe_allow_html=True,
        )
        return

    for lead in leads:
        _job_card(lead)


def _job_card(lead: dict):
    score     = _lead_score(lead)
    rank      = lead.get("rank", "?")
    company   = lead.get("company_name", "Unknown")
    role      = lead.get("role_title",   "Unknown Role")
    source    = lead.get("source_channel") or lead.get("source_type", "direct")
    stage_lbl = lead.get("company_stage_label", "")
    below     = lead.get("below_mesa_benchmark", False)
    match_id  = lead.get("job_id") or lead.get("id")
    posted    = lead.get("posted_at")
    location  = lead.get("location", "India")

    date_str  = _rel_date(posted)
    exp_label = f"#{rank}  ·  {company}  —  {role}   [{score:.0f}/100]"

    active_key = f"active_{match_id}"
    if active_key not in st.session_state:
        st.session_state[active_key] = None
    active = st.session_state[active_key]

    with st.expander(exp_label, expanded=True):
        col_lead, col_panel = st.columns([2.5, 2.5] if active else [4, 1])

        # ── Lead details (left) ────────────────────────────────────────────────
        with col_lead:
            header_right = f'<span style="font-size:12px;color:#6B7A99;">📍 {location}'
            if date_str:
                header_right += f" &nbsp;·&nbsp; 🕐 {date_str}"
            header_right += "</span>"
            badges_row = (
                _source_badge(source) +
                _stage_badge(stage_lbl) +
                (_badge("⚠ Below Mesa Floor", "warning") if below else "")
            )
            st.markdown(
                f'<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;">'
                f'<div>{badges_row}</div>'
                f'<div>{header_right}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

            st.markdown(_score_display(score), unsafe_allow_html=True)
            st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

            desc = (lead.get("description") or lead.get("snippet") or "")[:550]
            if desc:
                st.markdown(
                    f"<div style='font-size:13px;line-height:1.7;color:#374151;margin:8px 0 12px;'>{desc}</div>",
                    unsafe_allow_html=True,
                )

            axis_sc = lead.get("axis_scores") or {}
            if axis_sc:
                col_ax, col_rat = st.columns([1, 1], gap="large")
                with col_ax:
                    st.markdown('<span class="section-label">AI Axis Scores</span>', unsafe_allow_html=True)
                    st.markdown(f'<div style="margin:4px 0;">{_axis_bars(axis_sc)}</div>', unsafe_allow_html=True)
                with col_rat:
                    rat = lead.get("rationale", "")
                    if rat:
                        st.markdown('<span class="section-label">Why It Fits</span>', unsafe_allow_html=True)
                        st.markdown(
                            f'<div style="font-size:13px;color:#374151;line-height:1.6;'
                            f'background:#F8FAFC;border-radius:10px;padding:12px 14px;'
                            f'border-left:3px solid #E8521A;">'
                            f'<em>{rat}</em></div>',
                            unsafe_allow_html=True,
                        )
            else:
                rat = lead.get("rationale", "")
                if rat:
                    st.markdown(f"**Why it fits:** _{rat}_")

            st.markdown('<div class="mesa-div"></div>', unsafe_allow_html=True)
            hm_name = lead.get("hiring_manager_name")
            hm_li   = lead.get("hiring_manager_linkedin")
            col_hm, col_url_link = st.columns([2, 1])
            with col_hm:
                if hm_name:
                    hm_md = f"👤 **{hm_name}**"
                    if hm_li:
                        hm_md += f"  ·  [LinkedIn ↗]({hm_li})"
                    st.markdown(hm_md)
                else:
                    st.markdown(
                        '👤 Hiring manager not identified &nbsp;'
                        '<span class="badge badge-warning">Manual research needed</span>',
                        unsafe_allow_html=True,
                    )
            with col_url_link:
                url = lead.get("post_url") or lead.get("signal_url")
                if url:
                    st.markdown(f"[View posting ↗]({url})")

        # ── Action panel (right) — buttons + inline draft ─────────────────────
        with col_panel:
            if active:
                # Horizontal: 3 buttons side by side when panel is open
                bc1, bc2, bc3 = st.columns(3)
                with bc1:
                    btn_e = st.button("✉ Email", key=f"e_{match_id}", use_container_width=True,
                                      type="primary" if active == "email"    else "secondary")
                with bc2:
                    btn_d = st.button("🔗 DM",    key=f"d_{match_id}", use_container_width=True,
                                      type="primary" if active == "linkedin" else "secondary")
                with bc3:
                    btn_dos = st.button("📄 Dossier", key=f"dos_{match_id}", use_container_width=True,
                                        type="primary" if active == "dossier"  else "secondary")
            else:
                # Vertical: 3 buttons stacked when panel is closed
                btn_e   = st.button("✉  Draft Email",  key=f"e_{match_id}",   use_container_width=True, type="secondary")
                st.markdown("<div style='height:4px'></div>", unsafe_allow_html=True)
                btn_d   = st.button("🔗  LinkedIn DM", key=f"d_{match_id}",   use_container_width=True, type="secondary")
                st.markdown("<div style='height:4px'></div>", unsafe_allow_html=True)
                btn_dos = st.button("📄  Dossier",     key=f"dos_{match_id}", use_container_width=True, type="secondary")

            # Toggle: click again to close
            if btn_e:
                st.session_state[active_key] = None if active == "email"    else "email"
                st.rerun()
            if btn_d:
                st.session_state[active_key] = None if active == "linkedin" else "linkedin"
                st.rerun()
            if btn_dos:
                st.session_state[active_key] = None if active == "dossier"  else "dossier"
                st.rerun()

            # ── Email / LinkedIn draft ─────────────────────────────────────────
            if active in ("email", "linkedin"):
                st.markdown('<div class="mesa-div"></div>', unsafe_allow_html=True)
                outreach_key = f"outreach_{match_id}"
                if outreach_key not in st.session_state:
                    with st.spinner("Drafting outreach…"):
                        try:
                            from pipeline.s5_outreach import generate_outreach_on_demand
                            st.session_state[outreach_key] = generate_outreach_on_demand(
                                st.session_state.candidate_id, match_id
                            )
                        except Exception as exc:
                            st.error(f"Generation failed: {exc}")
                            st.session_state[outreach_key] = None

                draft = st.session_state[outreach_key]
                if draft:
                    if not draft.hiring_manager_identified:
                        st.markdown(
                            '<div style="background:#FEF3C7;border:1px solid #F59E0B;border-radius:8px;'
                            'padding:8px 12px;font-size:12px;color:#92400E;margin-bottom:6px;">'
                            '⚠ No hiring manager found.</div>',
                            unsafe_allow_html=True,
                        )
                    elif draft.email_found:
                        st.markdown(
                            f'<div style="background:#D1FAE5;border:1px solid #10B981;border-radius:8px;'
                            f'padding:8px 12px;font-size:12px;color:#065F46;margin-bottom:6px;">'
                            f'✓ {draft.hiring_manager_email}</div>',
                            unsafe_allow_html=True,
                        )
                    else:
                        st.markdown(
                            '<div style="background:#DBEAFE;border:1px solid #3B82F6;border-radius:8px;'
                            'padding:8px 12px;font-size:12px;color:#1E40AF;margin-bottom:6px;">'
                            '📧 Email not found — LinkedIn DM recommended.</div>',
                            unsafe_allow_html=True,
                        )

                    if active == "email":
                        st.markdown('<span class="section-label">Subject</span>', unsafe_allow_html=True)
                        st.code(draft.email.subject, language=None)
                        st.markdown('<span class="section-label">Email Body</span>', unsafe_allow_html=True)
                        st.text_area("Email body", value=draft.email.body, height=220,
                                     label_visibility="collapsed", key=f"ta_e_{match_id}")
                        st.button("📤 Send Email", key=f"send_e_{match_id}",
                                  use_container_width=True, disabled=True,
                                  help="Gmail integration coming soon")
                    else:
                        lbl = "LinkedIn DM" if draft.hiring_manager_identified else "Connection Note (≤ 300 chars)"
                        st.markdown(f'<span class="section-label">{lbl}</span>', unsafe_allow_html=True)
                        st.text_area("LinkedIn message", value=draft.dm, height=160,
                                     label_visibility="collapsed", key=f"ta_d_{match_id}")
                        if not draft.hiring_manager_identified:
                            char_count = len(draft.dm)
                            color = "#EF4444" if char_count > 290 else "#6B7A99"
                            st.markdown(
                                f'<span style="font-size:11px;color:{color};">{char_count} / 300 chars</span>',
                                unsafe_allow_html=True,
                            )
                        st.button("📤 Send LinkedIn DM", key=f"send_d_{match_id}",
                                  use_container_width=True, disabled=True,
                                  help="LinkedIn integration coming soon")

                    if draft.personalisation_note:
                        st.markdown(
                            f'<div style="background:#FFF8F5;border:1px solid #FED7B5;'
                            f'border-left:3px solid #E8521A;border-radius:8px;'
                            f'padding:10px 12px;font-size:12px;color:#374151;margin-top:8px;">'
                            f'<strong style="color:#E8521A;font-size:10px;text-transform:uppercase;'
                            f'letter-spacing:0.5px;">Before sending</strong><br>'
                            f'📌 {draft.personalisation_note}</div>',
                            unsafe_allow_html=True,
                        )

            # ── Dossier ───────────────────────────────────────────────────────
            elif active == "dossier":
                st.markdown('<div class="mesa-div"></div>', unsafe_allow_html=True)
                dossier_key = f"dossier_{match_id}"
                if dossier_key not in st.session_state:
                    with st.spinner("Researching company…"):
                        try:
                            from pipeline.s6_dossier import generate_dossier
                            st.session_state[dossier_key] = generate_dossier(
                                st.session_state.candidate_id, match_id
                            )
                        except Exception as exc:
                            st.error(f"Dossier generation failed: {exc}")
                            st.session_state[dossier_key] = None

                dossier = st.session_state[dossier_key]
                if dossier:
                    try:
                        import markdown as md_lib
                        html_body = md_lib.markdown(
                            dossier.markdown_content,
                            extensions=["tables", "fenced_code"],
                        )
                    except ImportError:
                        html_body = dossier.markdown_content.replace("\n", "<br>")
                    st.markdown(
                        f'<style>'
                        f'.dos-{match_id} h1{{font-size:14px!important;font-weight:800;color:#0B1929;margin:12px 0 5px;}}'
                        f'.dos-{match_id} h2{{font-size:13px!important;font-weight:700;color:#1E3A5F;margin:10px 0 4px;}}'
                        f'.dos-{match_id} h3{{font-size:13px!important;font-weight:600;color:#374151;margin:8px 0 3px;}}'
                        f'.dos-{match_id} p{{font-size:14px!important;line-height:1.85;color:#374151;margin:4px 0 8px;}}'
                        f'.dos-{match_id} li{{font-size:14px!important;line-height:1.7;color:#374151;margin:2px 0;}}'
                        f'.dos-{match_id} strong{{color:#0B1929!important;}}'
                        f'</style>'
                        f'<div class="dos-{match_id}" style="max-height:520px;overflow-y:auto;padding-right:4px;">'
                        f'{html_body}</div>',
                        unsafe_allow_html=True,
                    )
                    try:
                        from pipeline.s6_dossier import export_dossier_pdf
                        pdf = export_dossier_pdf(dossier.markdown_content)
                        st.download_button(
                            "⬇ Download PDF", data=pdf, file_name="mesa_dossier.pdf",
                            mime="application/pdf", key=f"dl_{match_id}",
                            use_container_width=True,
                        )
                    except Exception:
                        pass


# ── VIEW: draft ───────────────────────────────────────────────────────────────

def _view_draft():
    from pipeline.s5_outreach import generate_outreach_on_demand

    match_id     = st.session_state.selected_match_id
    candidate_id = st.session_state.candidate_id
    draft_type   = st.session_state.draft_type
    is_email     = draft_type == "email"

    col_back, col_title = st.columns([1, 5])
    with col_back:
        if st.button("← Back", key="back_draft"):
            _goto("results")
    with col_title:
        st.markdown(
            f'<h2 style="margin:0;font-size:1.4rem;">{"✉ Email Draft" if is_email else "🔗 LinkedIn Message"}</h2>',
            unsafe_allow_html=True,
        )

    st.markdown('<div class="mesa-div"></div>', unsafe_allow_html=True)

    col_job, col_draft = st.columns([1.15, 0.85], gap="large")

    with col_job:
        lead = next(
            (l for l in st.session_state.scored_leads
             if (l.get("job_id") or l.get("id")) == match_id),
            {},
        )
        if lead:
            score  = _lead_score(lead)
            source = lead.get("source_channel") or lead.get("source_type", "direct")
            st.markdown('<div class="mesa-card">', unsafe_allow_html=True)
            st.markdown(
                f'<div style="font-size:1.1rem;font-weight:800;color:#0B1929;margin-bottom:2px;">'
                f'{lead.get("company_name","Company")}</div>'
                f'<div style="font-size:13px;color:#6B7A99;margin-bottom:10px;">'
                f'{lead.get("role_title","Role")} · {lead.get("location","India")}</div>',
                unsafe_allow_html=True,
            )
            st.markdown(
                f'{_source_badge(source)} {_stage_badge(lead.get("company_stage_label",""))}',
                unsafe_allow_html=True,
            )
            st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
            st.markdown(_score_display(score), unsafe_allow_html=True)
            desc = (lead.get("description") or lead.get("snippet") or "")[:400]
            if desc:
                st.markdown(
                    f"<div style='font-size:12px;color:#6B7A99;margin-top:12px;line-height:1.65;'>"
                    f"{desc}…</div>",
                    unsafe_allow_html=True,
                )
            hm = lead.get("hiring_manager_name")
            if hm:
                st.markdown(f"<div style='margin-top:10px;font-size:13px;'>👤 **{hm}**</div>", unsafe_allow_html=True)
            rat = lead.get("rationale", "")
            if rat:
                st.markdown(
                    f'<div style="font-size:12px;color:#6B7A99;margin-top:8px;'
                    f'font-style:italic;line-height:1.55;">{rat}</div>',
                    unsafe_allow_html=True,
                )
            st.markdown("</div>", unsafe_allow_html=True)

    with col_draft:
        st.markdown('<div class="draft-panel">', unsafe_allow_html=True)

        if not st.session_state.outreach_draft:
            with st.spinner("Finding contact and drafting outreach with Gemini Pro…"):
                try:
                    st.session_state.outreach_draft = generate_outreach_on_demand(
                        candidate_id, match_id
                    )
                except Exception as exc:
                    st.error(f"Generation failed: {exc}")
                    st.markdown("</div>", unsafe_allow_html=True)
                    return

        draft = st.session_state.outreach_draft

        if not draft.hiring_manager_identified:
            st.warning("⚠ No hiring manager found — manual research required before sending.")
        elif draft.email_found:
            st.success(f"✓ Email found: {draft.hiring_manager_email}")
        else:
            st.info("Email not found — use the LinkedIn message.")

        st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)

        if is_email:
            st.markdown('<span class="section-label">Subject Line</span>', unsafe_allow_html=True)
            st.code(draft.email.subject, language=None)
            st.markdown('<span class="section-label">Email Body</span>', unsafe_allow_html=True)
            st.text_area(
                "body", value=draft.email.body, height=270,
                label_visibility="collapsed", key="ta_email_body",
            )
        else:
            lbl = (
                "LinkedIn DM — 3 sentences"
                if draft.hiring_manager_identified
                else "Connection Request Note (≤ 300 chars)"
            )
            st.markdown(f'<span class="section-label">{lbl}</span>', unsafe_allow_html=True)
            st.text_area(
                "dm", value=draft.dm, height=180,
                label_visibility="collapsed", key="ta_dm",
            )
            if not draft.hiring_manager_identified:
                char_count = len(draft.dm)
                color = "#EF4444" if char_count > 290 else "#6B7A99"
                st.markdown(
                    f'<span style="font-size:11px;color:{color};">{char_count} / 300 characters</span>',
                    unsafe_allow_html=True,
                )

        if draft.personalisation_note:
            st.markdown('<div class="mesa-div"></div>', unsafe_allow_html=True)
            st.markdown(
                f'<div style="background:#FFF8F5;border:1px solid #FED7B5;border-radius:10px;padding:12px 14px;">'
                f'<div style="font-size:10px;font-weight:700;color:#E8521A;text-transform:uppercase;letter-spacing:0.6px;margin-bottom:4px;">📌 Before sending</div>'
                f'<div style="font-size:12px;color:#374151;">{draft.personalisation_note}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

        if draft.outreach_hook:
            with st.expander("🪝 Outreach hook (reference)"):
                st.markdown(f"_{draft.outreach_hook}_")

        st.markdown("</div>", unsafe_allow_html=True)


# ── VIEW: dossier ─────────────────────────────────────────────────────────────

def _view_dossier():
    from pipeline.s6_dossier import generate_dossier, export_dossier_pdf

    match_id     = st.session_state.selected_match_id
    candidate_id = st.session_state.candidate_id

    col_back, col_dl = st.columns([4, 1])
    with col_back:
        if st.button("← Back to results", key="back_dossier"):
            _goto("results")
    with col_dl:
        if st.session_state.dossier_output:
            try:
                pdf = export_dossier_pdf(st.session_state.dossier_output.markdown_content)
                st.download_button(
                    "⬇ PDF", data=pdf,
                    file_name="mesa_dossier.pdf",
                    mime="application/pdf",
                    use_container_width=True,
                )
            except Exception:
                pass

    st.markdown('<div class="mesa-div"></div>', unsafe_allow_html=True)

    if not st.session_state.dossier_output:
        with st.spinner("Researching company and writing dossier with Gemini Pro…"):
            try:
                st.session_state.dossier_output = generate_dossier(candidate_id, match_id)
            except Exception as exc:
                st.error(f"Dossier generation failed: {exc}")
                return

    dossier = st.session_state.dossier_output

    st.markdown(
        f'<div class="mesa-header anim-down">'
        f'<p style="color:rgba(255,255,255,0.45);font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.8px;margin:0 0 6px;">Internal Dossier</p>'
        f'<h3 style="color:white;margin:0 0 4px;font-size:1.3rem;">{dossier.role_title} &nbsp;·&nbsp; {dossier.company_name}</h3>'
        f'<p style="color:rgba(255,255,255,0.55);margin:0;font-size:13px;">Prepared for {dossier.candidate_name}</p>'
        f'</div>',
        unsafe_allow_html=True,
    )

    try:
        import markdown as md_lib
        html_body = md_lib.markdown(
            dossier.markdown_content,
            extensions=["tables", "fenced_code"],
        )
    except ImportError:
        html_body = dossier.markdown_content.replace("\n", "<br>")

    st.markdown(
        f'<div class="mesa-card anim-up" style="line-height:1.75;font-size:14px;">{html_body}</div>',
        unsafe_allow_html=True,
    )

    try:
        pdf = export_dossier_pdf(dossier.markdown_content)
        st.download_button(
            "⬇ Download as PDF", data=pdf,
            file_name="mesa_dossier.pdf",
            mime="application/pdf",
        )
    except Exception:
        pass


# ── VIEW: history ─────────────────────────────────────────────────────────────

def _view_history():
    from db.queries import get_recent_candidates, get_matches_for_candidate

    col_h, col_new = st.columns([4, 1])
    with col_h:
        st.markdown(
            '<h2 style="margin:0 0 4px;font-size:1.5rem;">🕒 Past Runs</h2>'
            '<p style="color:#6B7A99;font-size:13px;margin:0;">Your recent pipeline executions</p>',
            unsafe_allow_html=True,
        )
    with col_new:
        if st.button("+ New Analysis", type="primary", use_container_width=True):
            _reset()

    st.markdown('<div class="mesa-div"></div>', unsafe_allow_html=True)

    try:
        candidates = get_recent_candidates(limit=20)
    except Exception as exc:
        st.error(f"Could not load history: {exc}")
        return

    if not candidates:
        st.markdown(
            '<div style="text-align:center;padding:48px 24px;">'
            '<div style="font-size:2.5rem;margin-bottom:12px;">📭</div>'
            '<div style="font-size:15px;font-weight:600;color:#0B1929;margin-bottom:6px;">No past runs yet</div>'
            '<div style="font-size:13px;color:#6B7A99;">Complete your first analysis to see it here.</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        return

    for cand in candidates:
        cid      = cand.get("id")
        name     = cand.get("name", "Unknown")
        status   = cand.get("pipeline_status", "unknown")
        created  = _rel_date(cand.get("created_at"))
        itype    = (cand.get("input_type") or "unknown").upper()
        roles    = cand.get("target_roles") or []
        top_role = (roles[0].get("title") if roles and isinstance(roles[0], dict) else "—")
        comp     = cand.get("compensation_band_inr") or "—"

        status_icon  = {"ranked": "✅", "error": "❌", "complete": "✅"}.get(status, "⏳")
        itype_badge  = _badge(itype, "direct")

        st.markdown(
            f'<div class="mesa-card" style="padding:18px 22px;margin-bottom:10px;display:flex;'
            f'align-items:center;justify-content:space-between;">'
            f'<div style="flex:1;min-width:0;">'
            f'<div style="font-size:15px;font-weight:700;color:#0B1929;margin-bottom:3px;">'
            f'{name} &nbsp;{itype_badge}&nbsp;'
            f'<span style="font-size:11px;color:#94A3B8;font-weight:400;">{created}</span>'
            f'</div>'
            f'<div style="font-size:12px;color:#6B7A99;">'
            f'Top role: <strong style="color:#374151;">{top_role}</strong>'
            f' &nbsp;·&nbsp; Comp: {comp}'
            f' &nbsp;·&nbsp; {status_icon} {status.replace("_"," ").title()}</div>'
            f'</div></div>',
            unsafe_allow_html=True,
        )
        if st.button("Load →", key=f"load_{cid}", use_container_width=False):
            try:
                matches = get_matches_for_candidate(cid)
                for m in matches:
                    if "job_id" not in m:
                        m["job_id"] = m.get("id")
                st.session_state.candidate_id    = cid
                st.session_state.scored_leads    = matches
                st.session_state.student_profile = None
                st.session_state.candidate_model = None
                st.session_state._hist_name      = name
                _goto("results")
            except Exception as exc:
                st.error(f"Could not load: {exc}")


# ── Router ────────────────────────────────────────────────────────────────────

def main():
    _init()
    st.markdown(_CSS, unsafe_allow_html=True)

    dispatch = {
        "input":     _view_input,
        "profiling": _view_profiling,
        "confirmed": _view_confirmed,
        "searching": _view_searching,
        "results":   _view_results,
        "draft":     _view_draft,
        "dossier":   _view_dossier,
        "history":   _view_history,
    }

    view     = st.session_state.get("view", "input")
    renderer = dispatch.get(view)

    if renderer:
        renderer()
    else:
        st.error(f"Unknown view: {view!r}")
        _reset()


if __name__ == "__main__":
    main()
