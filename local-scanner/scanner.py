#!/usr/bin/env python3
"""
Local File Scanner
------------------
Serves a web UI for browsing, searching, and previewing files on your LAN.

Usage:
    python3 scanner.py [directory] [--port 8080] [--host 0.0.0.0]
                       [--password SECRET] [--username admin]

Examples:
    python3 scanner.py                                # scan current directory, no auth
    python3 scanner.py ~/Movies --port 9000           # scan ~/Movies on port 9000
    python3 scanner.py ~/Movies --password hunter2    # require a password
"""

import os
import re
import sys
import json
import base64
import secrets
import socket
import argparse
import mimetypes
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote

# ---------------------------------------------------------------------------
# File-type classification
# ---------------------------------------------------------------------------

TEXT_EXTENSIONS = {
    '.txt', '.md', '.rst', '.py', '.js', '.ts', '.jsx', '.tsx',
    '.html', '.htm', '.css', '.scss', '.sass', '.less',
    '.json', '.yaml', '.yml', '.toml', '.ini', '.cfg', '.conf',
    '.sh', '.bash', '.zsh', '.fish', '.bat', '.ps1',
    '.c', '.cpp', '.h', '.hpp', '.java', '.go', '.rs', '.rb',
    '.php', '.swift', '.kt', '.r', '.sql', '.xml',
    '.csv', '.log', '.env', '.gitignore', '.editorconfig',
    '.dockerfile', '.makefile', '.mk',
}

IMAGE_EXTENSIONS = {
    '.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.ico',
    '.svg', '.tiff', '.tif', '.avif',
}

VIDEO_EXTENSIONS = {
    '.mp4', '.webm', '.mov', '.avi', '.mkv', '.m4v', '.ogv',
    '.flv', '.wmv', '.3gp', '.ts',
}

TEXT_BASENAMES = {
    'dockerfile', 'makefile', 'gemfile', 'rakefile', 'procfile',
    'license', 'copying', 'notice', 'authors', 'changelog',
    'readme', '.gitignore', '.gitattributes', '.env',
}

ROOT_DIR: Path = Path('.')

# Set by main() when --password is supplied; None means no auth required.
# Stored as (username_bytes, password_bytes) for secrets.compare_digest.
CREDENTIALS: tuple[bytes, bytes] | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        return s.getsockname()[0]
    except Exception:
        return '127.0.0.1'
    finally:
        s.close()


def classify(path: Path) -> str:
    ext = path.suffix.lower()
    name = path.name.lower()
    if ext in IMAGE_EXTENSIONS:
        return 'image'
    if ext in VIDEO_EXTENSIONS:
        return 'video'
    if ext in TEXT_EXTENSIONS or name in TEXT_BASENAMES:
        return 'text'
    return 'other'


def human_size(n: int) -> str:
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024:
            return f'{n:.1f} {unit}' if unit != 'B' else f'{n} B'
        n /= 1024
    return f'{n:.1f} PB'


def safe_path(raw: str) -> Path | None:
    """Resolve a client-supplied path, rejecting anything outside ROOT_DIR."""
    try:
        candidate = (ROOT_DIR / unquote(raw)).resolve()
        ROOT_DIR.resolve()
        candidate.relative_to(ROOT_DIR.resolve())  # raises ValueError if outside
        return candidate
    except (ValueError, Exception):
        return None


def file_info(path: Path) -> dict:
    try:
        stat = path.stat()
        size = stat.st_size if path.is_file() else 0
        mtime = stat.st_mtime
    except OSError:
        size, mtime = 0, 0
    return {
        'name': path.name,
        'path': str(path.relative_to(ROOT_DIR)).replace('\\', '/'),
        'is_dir': path.is_dir(),
        'size': size,
        'size_h': human_size(size),
        'modified': mtime,
        'type': '' if path.is_dir() else classify(path),
        'ext': path.suffix.lower(),
    }


def scan_dir(path: Path, depth: int = 0, max_depth: int = 64) -> list:
    if depth > max_depth:
        return []
    items = []
    try:
        entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except PermissionError:
        return []
    for entry in entries:
        if entry.name.startswith('.'):
            continue
        info = file_info(entry)
        if entry.is_dir():
            info['children'] = scan_dir(entry, depth + 1, max_depth)
        items.append(info)
    return items


# ---------------------------------------------------------------------------
# Embedded frontend
# ---------------------------------------------------------------------------

HTML = r"""<!DOCTYPE html>
<html lang="en" data-theme="light">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Local File Scanner</title>
<link id="hljs-theme" rel="stylesheet"
      href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<style>
:root {
  --bg: #f5f5f5;
  --surface: #ffffff;
  --sidebar-bg: #1e1e2e;
  --sidebar-text: #cdd6f4;
  --sidebar-hover: #313244;
  --sidebar-active: #45475a;
  --sidebar-folder: #89b4fa;
  --sidebar-image: #a6e3a1;
  --sidebar-video: #f38ba8;
  --sidebar-text-file: #cba6f7;
  --sidebar-other: #9399b2;
  --accent: #89b4fa;
  --border: #e0e0e0;
  --text: #1c1c2e;
  --muted: #888;
  --header-bg: #1e1e2e;
  --header-text: #cdd6f4;
  --preview-bg: #ffffff;
  --code-bg: #f6f8fa;
  --tag-bg: #e8f4fd;
  --tag-text: #0969da;
  --scrollbar: #c9d1d9;
}
[data-theme="dark"] {
  --bg: #11111b;
  --surface: #1e1e2e;
  --border: #313244;
  --text: #cdd6f4;
  --muted: #6c7086;
  --preview-bg: #181825;
  --code-bg: #181825;
  --tag-bg: #313244;
  --tag-text: #89b4fa;
  --scrollbar: #45475a;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  font-size: 14px;
  background: var(--bg);
  color: var(--text);
  height: 100vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* ── Header ── */
#header {
  background: var(--header-bg);
  color: var(--header-text);
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 0 16px;
  height: 48px;
  flex-shrink: 0;
  border-bottom: 1px solid rgba(255,255,255,0.06);
  z-index: 10;
}
#header h1 { font-size: 15px; font-weight: 600; white-space: nowrap; opacity: 0.9; }
#search-wrap {
  flex: 1;
  max-width: 440px;
  position: relative;
  margin-left: 8px;
}
#search {
  width: 100%;
  padding: 6px 12px 6px 32px;
  border-radius: 6px;
  border: 1px solid rgba(255,255,255,0.12);
  background: rgba(255,255,255,0.08);
  color: var(--header-text);
  font-size: 13px;
  outline: none;
  transition: border-color .15s;
}
#search::placeholder { color: rgba(205,214,244,0.45); }
#search:focus { border-color: var(--accent); }
#search-wrap::before {
  content: '⌕';
  position: absolute;
  left: 10px;
  top: 50%;
  transform: translateY(-50%);
  opacity: .5;
  font-size: 16px;
  pointer-events: none;
}
#header-right { margin-left: auto; display: flex; align-items: center; gap: 10px; }
#lan-addr {
  font-size: 11px;
  opacity: 0.55;
  font-family: monospace;
  white-space: nowrap;
}
#theme-btn {
  background: none;
  border: 1px solid rgba(255,255,255,0.15);
  color: var(--header-text);
  border-radius: 5px;
  padding: 4px 9px;
  cursor: pointer;
  font-size: 14px;
  line-height: 1;
  transition: background .15s;
}
#theme-btn:hover { background: rgba(255,255,255,0.1); }

/* ── Layout ── */
#layout {
  display: flex;
  flex: 1;
  overflow: hidden;
}

/* ── Sidebar ── */
#sidebar {
  width: 280px;
  min-width: 180px;
  max-width: 480px;
  background: var(--sidebar-bg);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  flex-shrink: 0;
  position: relative;
}
#sidebar-inner {
  flex: 1;
  overflow-y: auto;
  padding: 6px 0;
}
#sidebar-inner::-webkit-scrollbar { width: 5px; }
#sidebar-inner::-webkit-scrollbar-track { background: transparent; }
#sidebar-inner::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.1); border-radius: 3px; }

/* resize handle */
#resize-handle {
  position: absolute;
  right: 0;
  top: 0;
  bottom: 0;
  width: 4px;
  cursor: col-resize;
  background: transparent;
  z-index: 5;
}
#resize-handle:hover,
#resize-handle.dragging { background: var(--accent); }

/* ── Tree nodes ── */
.tree-node { user-select: none; }
.tree-label {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 3px 8px;
  cursor: pointer;
  color: var(--sidebar-text);
  border-radius: 4px;
  white-space: nowrap;
  overflow: hidden;
  transition: background .1s;
}
.tree-label:hover { background: var(--sidebar-hover); }
.tree-label.active { background: var(--sidebar-active); }
.tree-label.hidden { display: none; }
.tree-icon { font-size: 13px; flex-shrink: 0; }
.tree-name {
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 13px;
  flex: 1;
}
.tree-name mark { background: #f38ba855; color: inherit; border-radius: 2px; }
.tree-toggle { font-size: 10px; flex-shrink: 0; opacity: 0.5; width: 10px; text-align: center; }
.tree-children { margin-left: 16px; }
.tree-children.collapsed { display: none; }

/* type colors */
.type-folder .tree-icon { color: var(--sidebar-folder); }
.type-image  .tree-icon { color: var(--sidebar-image); }
.type-video  .tree-icon { color: var(--sidebar-video); }
.type-text   .tree-icon { color: var(--sidebar-text-file); }
.type-other  .tree-icon { color: var(--sidebar-other); }

/* ── Main ── */
#main {
  flex: 1;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  background: var(--bg);
}
#breadcrumb {
  padding: 8px 16px;
  font-size: 12px;
  color: var(--muted);
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  flex-shrink: 0;
}
#breadcrumb span { cursor: pointer; }
#breadcrumb span:hover { color: var(--accent); }
#preview {
  flex: 1;
  overflow: auto;
  padding: 24px;
  display: flex;
  flex-direction: column;
  align-items: center;
}
#preview::-webkit-scrollbar { width: 8px; height: 8px; }
#preview::-webkit-scrollbar-track { background: transparent; }
#preview::-webkit-scrollbar-thumb { background: var(--scrollbar); border-radius: 4px; }

/* ── Empty / placeholder ── */
#empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: var(--muted);
  text-align: center;
  gap: 12px;
}
#empty .icon { font-size: 56px; opacity: .3; }
#empty p { font-size: 15px; }
#empty small { font-size: 12px; opacity: .6; }

/* ── Image preview ── */
.img-wrap {
  width: 100%;
  display: flex;
  justify-content: center;
  align-items: flex-start;
}
.img-wrap img {
  max-width: 100%;
  max-height: calc(100vh - 160px);
  object-fit: contain;
  border-radius: 6px;
  box-shadow: 0 2px 16px rgba(0,0,0,.12);
  background: repeating-conic-gradient(#ccc 0% 25%, #fff 0% 50%) 0 0 / 16px 16px;
}

/* ── Video preview ── */
.video-wrap {
  width: 100%;
  display: flex;
  justify-content: center;
}
.video-wrap video {
  max-width: 100%;
  max-height: calc(100vh - 160px);
  border-radius: 6px;
  box-shadow: 0 2px 16px rgba(0,0,0,.18);
  background: #000;
}

/* ── Code / text preview ── */
.code-wrap {
  width: 100%;
  max-width: 980px;
}
.code-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 14px;
  background: #2d2d3f;
  border-radius: 6px 6px 0 0;
  font-size: 12px;
  color: #cdd6f4;
}
.code-lang { opacity: .6; }
.copy-btn {
  background: rgba(255,255,255,0.1);
  border: 1px solid rgba(255,255,255,0.15);
  color: #cdd6f4;
  border-radius: 4px;
  padding: 2px 10px;
  cursor: pointer;
  font-size: 11px;
  transition: background .15s;
}
.copy-btn:hover { background: rgba(255,255,255,0.2); }
.copy-btn.copied { color: #a6e3a1; }
pre.hljs-pre {
  margin: 0;
  border-radius: 0 0 6px 6px;
  overflow-x: auto;
  font-size: 13px;
  line-height: 1.6;
  background: var(--code-bg) !important;
  padding: 16px;
  border: 1px solid var(--border);
  border-top: none;
}
pre.hljs-pre code { background: none !important; }

/* ── Markdown preview ── */
.md-wrap {
  width: 100%;
  max-width: 820px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 32px 40px;
}
.md-wrap h1,h2,h3,h4 { margin: 1em 0 .4em; line-height: 1.3; }
.md-wrap h1 { font-size: 1.8em; border-bottom: 1px solid var(--border); padding-bottom: .3em; }
.md-wrap h2 { font-size: 1.4em; border-bottom: 1px solid var(--border); padding-bottom: .2em; }
.md-wrap p { margin: .6em 0; line-height: 1.7; }
.md-wrap ul,ol { margin: .5em 0 .5em 1.5em; }
.md-wrap li { margin: .2em 0; }
.md-wrap code { background: var(--code-bg); padding: 1px 5px; border-radius: 3px; font-size: .9em; font-family: monospace; }
.md-wrap pre { background: var(--code-bg); border-radius: 6px; padding: 12px 16px; overflow-x: auto; margin: 1em 0; border: 1px solid var(--border); }
.md-wrap pre code { background: none; padding: 0; }
.md-wrap blockquote { border-left: 3px solid var(--accent); padding-left: 12px; color: var(--muted); margin: 1em 0; }
.md-wrap table { border-collapse: collapse; margin: 1em 0; }
.md-wrap td,.md-wrap th { border: 1px solid var(--border); padding: 6px 12px; }
.md-wrap th { background: var(--code-bg); }
.md-wrap a { color: var(--accent); }
.md-wrap img { max-width: 100%; }

/* ── File-info card (for unknown types) ── */
.info-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 32px 40px;
  width: 100%;
  max-width: 520px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.info-card .big-icon { font-size: 52px; }
.info-card h2 { font-size: 18px; word-break: break-all; }
.info-meta { display: flex; flex-wrap: wrap; gap: 8px; }
.tag {
  background: var(--tag-bg);
  color: var(--tag-text);
  border-radius: 4px;
  padding: 3px 10px;
  font-size: 12px;
  font-family: monospace;
}
.download-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: var(--accent);
  color: #1e1e2e;
  font-weight: 600;
  border: none;
  border-radius: 6px;
  padding: 9px 18px;
  cursor: pointer;
  font-size: 13px;
  text-decoration: none;
  align-self: flex-start;
  transition: opacity .15s;
}
.download-btn:hover { opacity: .85; }

/* ── Status bar ── */
#statusbar {
  height: 24px;
  padding: 0 16px;
  background: var(--surface);
  border-top: 1px solid var(--border);
  display: flex;
  align-items: center;
  gap: 16px;
  font-size: 11px;
  color: var(--muted);
  flex-shrink: 0;
}
#statusbar span { white-space: nowrap; }

/* ── Search results ── */
#search-results {
  display: none;
  flex-direction: column;
  gap: 4px;
  width: 100%;
  max-width: 700px;
}
#search-results.visible { display: flex; }
.sr-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 6px;
  cursor: pointer;
  transition: border-color .15s, box-shadow .15s;
}
.sr-item:hover {
  border-color: var(--accent);
  box-shadow: 0 0 0 2px rgba(137,180,250,.15);
}
.sr-name { font-weight: 500; flex: 1; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.sr-name mark { background: #f38ba855; color: inherit; border-radius: 2px; }
.sr-path { font-size: 11px; color: var(--muted); font-family: monospace; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 300px; }
.sr-icon { flex-shrink: 0; }
.no-results { color: var(--muted); font-size: 13px; margin-top: 16px; }
</style>
</head>
<body>

<div id="header">
  <h1>📂 Local Scanner</h1>
  <div id="search-wrap">
    <input id="search" type="search" placeholder="Search files…" autocomplete="off" spellcheck="false">
  </div>
  <div id="header-right">
    <span id="lan-addr"></span>
    <button id="theme-btn" title="Toggle dark/light">🌙</button>
  </div>
</div>

<div id="layout">
  <div id="sidebar">
    <div id="sidebar-inner"></div>
    <div id="resize-handle"></div>
  </div>

  <div id="main">
    <div id="breadcrumb">Root</div>
    <div id="preview">
      <div id="empty">
        <div class="icon">🗂️</div>
        <p>Select a file to preview</p>
        <small>Images, videos, text, and code all display inline</small>
      </div>
    </div>
  </div>
</div>

<div id="statusbar">
  <span id="sb-path">No file selected</span>
  <span id="sb-size"></span>
  <span id="sb-type"></span>
  <span id="sb-count" style="margin-left:auto"></span>
</div>

<script>
// ── State ──────────────────────────────────────────────────────────────────
let allFiles = [];          // flat list of all file nodes
let activeNode = null;      // currently selected file path
let treeData = [];

// ── Boot ───────────────────────────────────────────────────────────────────
fetch('/api/tree').then(r => r.json()).then(data => {
  treeData = data.tree;
  collectFiles(treeData);
  renderTree(treeData, document.getElementById('sidebar-inner'));
  document.getElementById('lan-addr').textContent = data.lan_addr;
  document.getElementById('sb-count').textContent =
    allFiles.length + ' files';
});

function collectFiles(nodes) {
  for (const n of nodes) {
    if (!n.is_dir) allFiles.push(n);
    if (n.children) collectFiles(n.children);
  }
}

// ── Tree rendering ─────────────────────────────────────────────────────────
const TYPE_ICON = {
  folder: '📁',
  image:  '🖼',
  video:  '🎬',
  text:   '📄',
  other:  '📎',
};

function renderTree(nodes, container, indent = 0) {
  for (const node of nodes) {
    const wrapper = document.createElement('div');
    wrapper.className = 'tree-node';

    const label = document.createElement('div');
    label.className = 'tree-label type-' + (node.is_dir ? 'folder' : (node.type || 'other'));
    label.style.paddingLeft = (8 + indent * 14) + 'px';
    label.dataset.path = node.path;
    label.dataset.type = node.is_dir ? 'folder' : (node.type || 'other');

    const toggle = document.createElement('span');
    toggle.className = 'tree-toggle';
    toggle.textContent = node.is_dir ? '▶' : '';

    const icon = document.createElement('span');
    icon.className = 'tree-icon';
    icon.textContent = TYPE_ICON[node.is_dir ? 'folder' : (node.type || 'other')];

    const name = document.createElement('span');
    name.className = 'tree-name';
    name.textContent = node.name;

    label.append(toggle, icon, name);
    wrapper.appendChild(label);

    if (node.is_dir && node.children) {
      const children = document.createElement('div');
      children.className = 'tree-children collapsed';
      renderTree(node.children, children, 0);
      wrapper.appendChild(children);

      label.addEventListener('click', () => {
        const collapsed = children.classList.toggle('collapsed');
        toggle.textContent = collapsed ? '▶' : '▼';
        label.classList.toggle('active', !collapsed);
      });
    } else {
      label.addEventListener('click', () => selectFile(node));
    }

    container.appendChild(wrapper);
  }
}

// ── File selection ─────────────────────────────────────────────────────────
function selectFile(node) {
  // deselect old
  document.querySelectorAll('.tree-label.active').forEach(el => {
    if (el.dataset.type !== 'folder') el.classList.remove('active');
  });
  // mark active
  const el = document.querySelector(`.tree-label[data-path="${CSS.escape(node.path)}"]`);
  if (el) el.classList.add('active');

  activeNode = node;
  updateBreadcrumb(node.path);
  updateStatusBar(node);
  renderPreview(node);
}

function updateBreadcrumb(path) {
  const parts = path.split('/');
  const bc = document.getElementById('breadcrumb');
  bc.innerHTML = parts.map((p, i) => {
    if (i === parts.length - 1) return `<strong>${esc(p)}</strong>`;
    return `<span>${esc(p)}</span> / `;
  }).join('');
}

function updateStatusBar(node) {
  document.getElementById('sb-path').textContent = node.path;
  document.getElementById('sb-size').textContent = node.size_h;
  document.getElementById('sb-type').textContent = node.type ? node.type.toUpperCase() : '';
}

// ── Preview rendering ──────────────────────────────────────────────────────
const preview = document.getElementById('preview');

function renderPreview(node) {
  preview.innerHTML = '';
  const fileUrl = '/api/file?path=' + encodeURIComponent(node.path);

  if (node.type === 'image') {
    const wrap = document.createElement('div');
    wrap.className = 'img-wrap';
    const img = document.createElement('img');
    img.src = fileUrl;
    img.alt = node.name;
    wrap.appendChild(img);
    preview.appendChild(wrap);

  } else if (node.type === 'video') {
    const wrap = document.createElement('div');
    wrap.className = 'video-wrap';
    const vid = document.createElement('video');
    vid.controls = true;
    vid.src = fileUrl;
    vid.preload = 'metadata';
    wrap.appendChild(vid);
    preview.appendChild(wrap);

  } else if (node.type === 'text') {
    const isMarkdown = node.ext === '.md' || node.ext === '.markdown';
    fetch(fileUrl).then(r => {
      if (!r.ok) throw new Error('Cannot read file');
      return r.text();
    }).then(text => {
      if (isMarkdown) {
        const wrap = document.createElement('div');
        wrap.className = 'md-wrap';
        wrap.innerHTML = marked.parse(text);
        // syntax highlight inside markdown
        wrap.querySelectorAll('pre code').forEach(el => hljs.highlightElement(el));
        preview.appendChild(wrap);
      } else {
        const wrap = document.createElement('div');
        wrap.className = 'code-wrap';

        const header = document.createElement('div');
        header.className = 'code-header';
        const langSpan = document.createElement('span');
        langSpan.className = 'code-lang';
        langSpan.textContent = node.name;
        const copyBtn = document.createElement('button');
        copyBtn.className = 'copy-btn';
        copyBtn.textContent = 'Copy';
        copyBtn.addEventListener('click', () => {
          navigator.clipboard.writeText(text).then(() => {
            copyBtn.textContent = 'Copied!';
            copyBtn.classList.add('copied');
            setTimeout(() => {
              copyBtn.textContent = 'Copy';
              copyBtn.classList.remove('copied');
            }, 1500);
          });
        });
        header.append(langSpan, copyBtn);

        const pre = document.createElement('pre');
        pre.className = 'hljs-pre';
        const code = document.createElement('code');
        code.textContent = text;
        // map extension to hljs language
        const lang = EXT_LANG[node.ext] || '';
        if (lang) code.className = `language-${lang}`;
        pre.appendChild(code);
        hljs.highlightElement(code);

        wrap.append(header, pre);
        preview.appendChild(wrap);
      }
    }).catch(() => {
      preview.innerHTML = '<div class="no-results" style="margin-top:40px">⚠ Could not load file contents.</div>';
    });

  } else {
    // generic info card
    const card = document.createElement('div');
    card.className = 'info-card';
    card.innerHTML = `
      <div class="big-icon">${TYPE_ICON[node.type] || '📎'}</div>
      <h2>${esc(node.name)}</h2>
      <div class="info-meta">
        <span class="tag">${esc(node.ext || 'no ext')}</span>
        <span class="tag">${esc(node.size_h)}</span>
      </div>
      <a class="download-btn" href="${fileUrl}" download="${esc(node.name)}">⬇ Download</a>
    `;
    preview.appendChild(card);
  }
}

// Extension → highlight.js language map
const EXT_LANG = {
  '.py': 'python', '.js': 'javascript', '.ts': 'typescript',
  '.jsx': 'javascript', '.tsx': 'typescript',
  '.html': 'html', '.htm': 'html', '.css': 'css', '.scss': 'scss',
  '.json': 'json', '.yaml': 'yaml', '.yml': 'yaml', '.toml': 'toml',
  '.sh': 'bash', '.bash': 'bash', '.zsh': 'bash',
  '.c': 'c', '.cpp': 'cpp', '.h': 'cpp', '.hpp': 'cpp',
  '.java': 'java', '.go': 'go', '.rs': 'rust', '.rb': 'ruby',
  '.php': 'php', '.swift': 'swift', '.kt': 'kotlin',
  '.sql': 'sql', '.xml': 'xml', '.r': 'r',
  '.csv': 'plaintext', '.txt': 'plaintext', '.log': 'plaintext',
};

// ── Search ─────────────────────────────────────────────────────────────────
const searchInput = document.getElementById('search');
searchInput.addEventListener('input', () => {
  const q = searchInput.value.trim().toLowerCase();
  if (!q) {
    showTree();
  } else {
    showSearchResults(q);
  }
});

function showTree() {
  document.getElementById('sidebar-inner').style.display = '';
  const sr = document.getElementById('search-results');
  if (sr) sr.classList.remove('visible');
  const emptyEl = document.getElementById('empty');
  if (emptyEl && !activeNode) emptyEl.style.display = '';
  // restore normal preview if nothing active
}

function showSearchResults(q) {
  document.getElementById('sidebar-inner').style.display = '';

  // filter tree labels
  const labels = document.querySelectorAll('.tree-label');
  labels.forEach(label => {
    if (!label.dataset.type || label.dataset.type === 'folder') return;
    const name = label.querySelector('.tree-name').textContent.toLowerCase();
    if (name.includes(q)) {
      label.classList.remove('hidden');
    } else {
      label.classList.add('hidden');
    }
  });

  // show search results panel overlaid on preview
  let sr = document.getElementById('search-results');
  if (!sr) {
    sr = document.createElement('div');
    sr.id = 'search-results';
    preview.parentElement.insertBefore(sr, preview);
  }

  const matches = allFiles.filter(f =>
    f.name.toLowerCase().includes(q) || f.path.toLowerCase().includes(q)
  );

  sr.innerHTML = '';
  sr.classList.add('visible');

  if (matches.length === 0) {
    sr.innerHTML = '<div class="no-results">No files match "' + esc(q) + '"</div>';
    return;
  }

  // limit to 200 results for performance
  matches.slice(0, 200).forEach(node => {
    const item = document.createElement('div');
    item.className = 'sr-item';
    item.innerHTML = `
      <span class="sr-icon">${TYPE_ICON[node.type] || '📎'}</span>
      <span class="sr-name">${highlight(esc(node.name), q)}</span>
      <span class="sr-path">${esc(node.path)}</span>
    `;
    item.addEventListener('click', () => {
      // clear search, scroll tree to file, open preview
      searchInput.value = '';
      showTree();
      selectFile(node);
      scrollToNode(node.path);
    });
    sr.appendChild(item);
  });

  if (matches.length > 200) {
    const note = document.createElement('div');
    note.className = 'no-results';
    note.textContent = `…and ${matches.length - 200} more. Narrow your search.`;
    sr.appendChild(note);
  }
}

// re-show tree node by scrolling/expanding parents
function scrollToNode(path) {
  const el = document.querySelector(`.tree-label[data-path="${CSS.escape(path)}"]`);
  if (!el) return;
  // expand all ancestors
  let parent = el.parentElement;
  while (parent) {
    if (parent.classList.contains('tree-children')) {
      parent.classList.remove('collapsed');
      const toggle = parent.previousSibling?.querySelector?.('.tree-toggle');
      if (toggle) toggle.textContent = '▼';
    }
    parent = parent.parentElement;
  }
  setTimeout(() => el.scrollIntoView({ block: 'nearest', behavior: 'smooth' }), 50);
}

function highlight(text, q) {
  const escaped = q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return text.replace(new RegExp(`(${escaped})`, 'gi'), '<mark>$1</mark>');
}

function esc(s) {
  return String(s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ── Dark / light theme ─────────────────────────────────────────────────────
const themeBtn = document.getElementById('theme-btn');
const hljsTheme = document.getElementById('hljs-theme');

function applyTheme(dark) {
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  themeBtn.textContent = dark ? '☀️' : '🌙';
  hljsTheme.href = dark
    ? 'https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css'
    : 'https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github.min.css';
  localStorage.setItem('lfs-theme', dark ? 'dark' : 'light');
}

themeBtn.addEventListener('click', () => {
  applyTheme(document.documentElement.dataset.theme !== 'dark');
});

// restore saved theme
const savedTheme = localStorage.getItem('lfs-theme');
if (savedTheme) applyTheme(savedTheme === 'dark');

// ── Sidebar resize ─────────────────────────────────────────────────────────
const sidebar = document.getElementById('sidebar');
const handle = document.getElementById('resize-handle');
let resizing = false, startX = 0, startW = 0;

handle.addEventListener('mousedown', e => {
  resizing = true;
  startX = e.clientX;
  startW = sidebar.offsetWidth;
  handle.classList.add('dragging');
  document.body.style.cursor = 'col-resize';
  document.body.style.userSelect = 'none';
});
document.addEventListener('mousemove', e => {
  if (!resizing) return;
  const w = Math.min(480, Math.max(160, startW + e.clientX - startX));
  sidebar.style.width = w + 'px';
});
document.addEventListener('mouseup', () => {
  if (resizing) {
    resizing = false;
    handle.classList.remove('dragging');
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
  }
});

// ── Keyboard shortcut: / to focus search ──────────────────────────────────
document.addEventListener('keydown', e => {
  if (e.key === '/' && document.activeElement !== searchInput) {
    e.preventDefault();
    searchInput.focus();
    searchInput.select();
  }
  if (e.key === 'Escape' && document.activeElement === searchInput) {
    searchInput.value = '';
    searchInput.blur();
    showTree();
  }
});
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # suppress default Apache-style logging

    # -- Basic Auth check ----------------------------------------------------
    def _check_auth(self) -> bool:
        """Return True if the request is authorised (or no auth is required)."""
        if CREDENTIALS is None:
            return True
        auth_header = self.headers.get('Authorization', '')
        if not auth_header.startswith('Basic '):
            self._demand_auth()
            return False
        try:
            decoded = base64.b64decode(auth_header[6:]).split(b':', 1)
            if len(decoded) != 2:
                raise ValueError
            supplied_user, supplied_pass = decoded
        except Exception:
            self._demand_auth()
            return False
        expected_user, expected_pass = CREDENTIALS
        # Use compare_digest for both to avoid timing attacks
        ok = (secrets.compare_digest(supplied_user, expected_user) and
              secrets.compare_digest(supplied_pass, expected_pass))
        if not ok:
            self._demand_auth()
        return ok

    def _demand_auth(self):
        self.send_response(401)
        self.send_header('WWW-Authenticate', 'Basic realm="Local File Scanner"')
        self.send_header('Content-Type', 'text/plain')
        self.send_header('Content-Length', '13')
        self.end_headers()
        self.wfile.write(b'Unauthorised.')

    def do_GET(self):
        if not self._check_auth():
            return

        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == '/':
            self._html()
        elif path == '/api/tree':
            self._tree()
        elif path == '/api/file':
            self._file(qs)
        else:
            self.send_error(404)

    # -- serve the SPA -------------------------------------------------------
    def _html(self):
        data = HTML.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # -- directory tree -------------------------------------------------------
    def _tree(self):
        tree = scan_dir(ROOT_DIR)
        payload = json.dumps({
            'tree': tree,
            'root': str(ROOT_DIR.resolve()),
            'lan_addr': f'http://{get_lan_ip()}:{self.server.server_address[1]}',
        }).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    # -- serve a file (with range support for video) -------------------------
    def _file(self, qs):
        raw = qs.get('path', [''])[0]
        if not raw:
            self.send_error(400, 'Missing path')
            return

        filepath = safe_path(raw)
        if filepath is None or not filepath.is_file():
            self.send_error(404, 'Not found')
            return

        mime, _ = mimetypes.guess_type(str(filepath))
        if mime is None:
            ext = filepath.suffix.lower()
            mime = 'text/plain' if ext in TEXT_EXTENSIONS else 'application/octet-stream'

        file_size = filepath.stat().st_size
        range_header = self.headers.get('Range')

        if range_header:
            m = re.match(r'bytes=(\d+)-(\d*)', range_header)
            if not m:
                self.send_error(416, 'Unsupported Range format')
                return
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) else file_size - 1
            end = min(end, file_size - 1)
            length = end - start + 1

            self.send_response(206)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Range', f'bytes {start}-{end}/{file_size}')
            self.send_header('Content-Length', str(length))
            self.send_header('Accept-Ranges', 'bytes')
            self.end_headers()

            try:
                with open(filepath, 'rb') as f:
                    f.seek(start)
                    remaining = length
                    while remaining > 0:
                        chunk = f.read(min(65536, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass
        else:
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(file_size))
            self.send_header('Accept-Ranges', 'bytes')
            self.end_headers()

            try:
                with open(filepath, 'rb') as f:
                    while True:
                        chunk = f.read(65536)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    global ROOT_DIR, CREDENTIALS

    parser = argparse.ArgumentParser(
        description='Local File Scanner — browse files on your LAN via a web UI',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        'directory',
        nargs='?',
        default='.',
        help='Root directory to scan (default: current directory)',
    )
    parser.add_argument(
        '--port', '-p',
        type=int,
        default=8080,
        help='Port to listen on (default: 8080)',
    )
    parser.add_argument(
        '--host',
        default='0.0.0.0',
        help='Interface to bind (default: 0.0.0.0 = all interfaces)',
    )
    parser.add_argument(
        '--password',
        default=None,
        help='Require this password to access the UI (HTTP Basic Auth)',
    )
    parser.add_argument(
        '--username',
        default='admin',
        help='Username for Basic Auth (default: admin)',
    )
    args = parser.parse_args()

    root = Path(args.directory).expanduser().resolve()
    if not root.is_dir():
        print(f'Error: "{root}" is not a directory.', file=sys.stderr)
        sys.exit(1)

    ROOT_DIR = root
    lan_ip = get_lan_ip()

    if args.password:
        CREDENTIALS = (args.username.encode(), args.password.encode())

    server = HTTPServer((args.host, args.port), Handler)

    print(f'\n  Local File Scanner')
    print(f'  ──────────────────────────────────────────')
    print(f'  Scanning : {root}')
    print(f'  Local    : http://localhost:{args.port}')
    print(f'  LAN      : http://{lan_ip}:{args.port}')
    if CREDENTIALS:
        print(f'  Auth     : username={args.username}  password={"*" * len(args.password)}')
    else:
        print(f'  Auth     : none (use --password to require a password)')
    print(f'\n  Press Ctrl+C to stop.\n')

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n  Stopped.')


if __name__ == '__main__':
    main()
