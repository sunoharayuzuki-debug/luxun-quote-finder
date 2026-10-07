"""Offline, evidence-first retrieval. No model calls or remote services."""
import re
import sqlite3
import unicodedata
from difflib import SequenceMatcher


def normalize(text):
    return ''.join(c.lower() for c in unicodedata.normalize('NFKC', text)
                   if unicodedata.category(c)[0] in 'LN')


def normalized_positions(text):
    chars, positions = [], []
    for i, char in enumerate(text):
        for c in normalize(char):
            chars.append(c)
            positions.append(i)
    return ''.join(chars), positions


def connect(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    return db


def excerpt(text, query, limit=160):
    normalized, positions = normalized_positions(text)
    start = normalized.find(query)
    if start < 0:
        return text[:limit], None
    a, b = positions[start], positions[start + len(query) - 1] + 1
    left, right = max(0, a - 65), min(len(text), b + 100)
    return text[left:right], [a-left, b-left]


def search(db, query, volume=None, approximate=False, limit=40):
    q = normalize(query)
    if len(q) < 2:
        raise ValueError('请输入至少两个汉字或字母。')
    if len(q) > 500:
        raise ValueError('请将查询控制在 500 个字以内。')
    where, args = (' AND volume=?', [int(volume)]) if volume else ('', [])
    # At corpus scale, scanning the normalized in-memory working set is both
    # deterministic and avoids tokenization problems with Chinese FTS defaults.
    rows = db.execute('SELECT * FROM pages WHERE instr(search_text, ?) > 0' +
                      where + ' ORDER BY volume,pdf_page', [q] + args).fetchall()
    rows = [r for r in rows if not r['boundary'] or any(
        m.start() < r['boundary'] < m.start()+len(q)
        for m in re.finditer(re.escape(q), r['search_text']))]
    total = len(rows)
    matches = [(r, 'exact', 1.0, q) for r in rows[:limit]]
    if not matches and approximate:
        # Candidate retrieval by overlapping character trigrams; similarity is
        # text similarity, NEVER a claim of historical certainty.
        grams = list(dict.fromkeys(q[i:i+3] for i in range(len(q)-2)))
        if len(q) < 6:
            return {'results': [], 'total': 0, 'message': '近似查询请至少输入六个字。'}
        grams = grams[::max(1, len(grams)//20)][:24]
        candidates = db.execute('SELECT * FROM pages WHERE boundary=0 AND (' +
            ' OR '.join('instr(search_text,?)>0' for _ in grams) + ')' + where,
            grams + args).fetchall()
        ranked = []
        for row in candidates:
            t = row['search_text']
            hits = sum(g in t for g in grams)
            if hits >= min(2, len(grams)):
                ranked.append((hits, row))
        scored = []
        for _, row in sorted(ranked, key=lambda x: -x[0])[:120]:
            t = row['search_text']
            anchor = SequenceMatcher(None, q, t, autojunk=False).find_longest_match()
            base = anchor.b-anchor.a
            best = (0, '')
            for shift in range(-3, 4):
                start = max(0, base+shift)
                candidate = t[start:start+len(q)]
                score = SequenceMatcher(None, q, candidate, autojunk=False).ratio()
                if score > best[0]:
                    best = (score, candidate)
            if best[0] >= .68:
                scored.append((row, 'approximate', best[0], best[1]))
        matches = sorted(scored, key=lambda x: -x[2])[:limit]
        total = len(scored)
    results = []
    for row, kind, score, target in matches:
        item = {k: row[k] for k in ['id','volume','pdf_page','printed_page',
                'page_status','work_id','section','boundary']}
        text, highlight = excerpt(row['display_text'], target)
        item.update(snippet=text, highlight=highlight, match_type=kind,
                    similarity=round(score, 3), work=None, notes=[])
        if row['work_id']:
            work = db.execute('SELECT * FROM works WHERE id=?', (row['work_id'],)).fetchone()
            item['work'] = dict(work) if work else None
            item['notes'] = [dict(n) for n in db.execute(
                'SELECT id,pdf_page,printed_page,raw,status FROM notes WHERE work_id=?',
                (row['work_id'],))]
        results.append(item)
    return {'results': results, 'total': total,
            'message': '未命中不代表鲁迅未写过；可能存在 OCR 错字、异文或断页。' if not results else ''}
