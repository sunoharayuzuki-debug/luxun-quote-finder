"""Extract local OCR PDFs, retain page evidence, derive reviewable TOC links."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time

from pypdf import PdfReader
from search_core import normalize

ROOT = Path(__file__).resolve().parent
VOLUME = re.compile(r'第\s*([0-9]+)\s*卷')
TOC = re.compile(r'^(.+?)[…⋯.·]{2,}\s*(\d{1,4})\s*$')
NOTE_START = re.compile(r'(?:本篇|本文|本诗|本信|此文|此篇).{0,35}?(?:最初|最早|初次|初刊|发表|刊载|收入)')
NOTE_END = re.compile(r'(?:〔|\[|【|\()\s*[2-9]\d*\s*(?:〕|\]|】|\))')


def footer_number(text):
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    if lines and re.fullmatch(r'\d{1,4}', lines[-1]):
        return int(lines[-1])
    return None


def clean_page(text):
    lines = text.replace('\x00', '').splitlines()
    if lines and '鲁迅全集' in normalize(lines[0]):
        lines.pop(0)
    if lines and re.fullmatch(r'\s*\d{1,4}\s*', lines[-1]):
        lines.pop()
    return '\n'.join(lines).strip()


def extract(path, cache):
    stat = path.stat()
    key = hashlib.sha256((str(path.resolve()) + str(stat.st_size) + str(stat.st_mtime_ns)).encode()).hexdigest()
    dest = cache / (key + '.json')
    if dest.exists():
        return json.loads(dest.read_text())
    reader = PdfReader(path)
    texts = [p.extract_text() or '' for p in reader.pages]
    dest.write_text(json.dumps(texts, ensure_ascii=False))
    return texts


def page_labels(texts):
    observed = [footer_number(t) for t in texts]
    labels = []
    for i, number in enumerate(observed):
        # Require agreement with nearby footer observations, never a global offset.
        votes = Counter(j+1-n for j, n in enumerate(observed[max(0,i-4):i+5], start=max(0,i-4))
                        if n is not None)
        if not votes:
            labels.append((None, 'unknown'))
            continue
        offset, count = votes.most_common(1)[0]
        predicted = i+1-offset
        if number is not None and i+1-number == offset and count >= 3:
            labels.append((number, 'ocr_consistent'))
        elif count >= 4 and predicted > 0:
            labels.append((predicted, 'inferred'))
        else:
            labels.append((None, 'unknown'))
    return labels


def contents(texts, labels):
    entries = []
    for i, text in enumerate(texts[:70]):
        lines = text.splitlines()
        matches = [TOC.match(line.strip()) for line in lines]
        matches = [m for m in matches if m]
        if len(matches) < 3:
            continue
        for m in matches:
            title, page = m.group(1).strip(), int(m.group(2))
            if page <= 0:
                continue
            target = next((j for j, (n, _) in enumerate(labels) if n == page), None)
            # Preserve all TOC boundaries, including failed title checks: dropping a
            # boundary would incorrectly attribute the next work to its predecessor.
            if target is not None:
                heading = normalize(clean_page(texts[target])[:180])
                status = 'toc_heading_agree' if normalize(title) in heading else 'toc_only'
                entries.append({'title': title, 'start': target+1, 'printed_start': page,
                                'toc_pdf_page': i+1, 'status': status})
    unique = {e['start']: e for e in entries}
    return sorted(unique.values(), key=lambda e:e['start'])


def build(source, output):
    output.parent.mkdir(parents=True, exist_ok=True)
    cache = output.parent / 'cache'
    cache.mkdir(exist_ok=True)
    files, skipped = {}, []
    for path in sorted(source.glob('*.pdf')):
        match = VOLUME.search(path.name)
        if not match:
            skipped.append({'file':path.name,'reason':'卷号无法识别'})
            continue
        v = int(match.group(1))
        # Prefer filename without duplicate suffix. Do not modify source files.
        if v in files:
            old = files[v]
            chosen = min([old,path], key=lambda p: ('(1)' in p.name, len(p.name), p.name))
            skipped.append({'file': (path if chosen == old else old).name,
                            'reason': f'同卷候选文件未导入，采用 {chosen.name}；未断言二者内容一致'})
            files[v] = chosen
        else:
            files[v] = path
    if not files:
        raise SystemExit('没有找到文件名含“第01卷”或“第1卷”的 PDF。')
    temp = output.with_suffix('.building.sqlite')
    if temp.exists():
        temp.unlink()
    db = sqlite3.connect(temp)
    db.executescript('''
    CREATE TABLE sources(volume INTEGER PRIMARY KEY,path TEXT,pages INTEGER,size INTEGER,mtime_ns INTEGER);
    CREATE TABLE works(id TEXT PRIMARY KEY,volume INTEGER,title TEXT,start_pdf INTEGER,end_pdf INTEGER,
        printed_start INTEGER,toc_pdf_page INTEGER,status TEXT);
    CREATE TABLE pages(id INTEGER PRIMARY KEY,volume INTEGER,pdf_page INTEGER,printed_page INTEGER,
        page_status TEXT,work_id TEXT,section TEXT,display_text TEXT,search_text TEXT,boundary INTEGER DEFAULT 0);
    CREATE TABLE notes(id INTEGER PRIMARY KEY,work_id TEXT,volume INTEGER,pdf_page INTEGER,
        printed_page INTEGER,raw TEXT,status TEXT);
    CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT);
    CREATE INDEX pages_volume ON pages(volume,pdf_page);
    CREATE INDEX notes_work ON notes(work_id);
    ''')
    report = {'volumes': [], 'skipped': skipped, 'warnings': [
        '文本来自用户提供的 OCR；版本与印次尚未逐卷人工核验。',
        '篇目由目录自动关联；注释为自动提取候选，不等于人工核定的初刊信息。',
        '页码为邻页一致性校验后的 OCR 值或推算值；正式引用须核对扫描页。']}
    for v,path in sorted(files.items()):
        print(f'第 {v:02d} 卷：提取 {path.name}', flush=True)
        texts = extract(path, cache)
        labels = page_labels(texts)
        entries = contents(texts, labels)
        stat = path.stat()
        db.execute('INSERT INTO sources VALUES(?,?,?,?,?)', (v,str(path.resolve()),len(texts),stat.st_size,stat.st_mtime_ns))
        work_for = {}
        for k, entry in enumerate(entries):
            end = entries[k+1]['start']-1 if k+1 < len(entries) else len(texts)
            wid = f'v{v:02d}-p{entry["start"]:04d}'
            db.execute('INSERT INTO works VALUES(?,?,?,?,?,?,?,?)',
                       (wid,v,entry['title'],entry['start'],end,entry['printed_start'],entry['toc_pdf_page'],entry['status']))
            for p in range(entry['start'], end+1):
                work_for[p] = wid
        cleaned = [clean_page(t) for t in texts]
        note_count = 0
        for i,text in enumerate(cleaned):
            page = i+1
            number,status = labels[i]
            wid = work_for.get(page)
            # Page headers distinguish collection when OCR retains them.
            header = texts[i].splitlines()[0] if texts[i].splitlines() else ''
            section = header if '鲁迅全集' in normalize(header) else ''
            db.execute('INSERT INTO pages(volume,pdf_page,printed_page,page_status,work_id,section,display_text,search_text) VALUES(?,?,?,?,?,?,?,?)',
                       (v,page,number,status,wid,section,text,normalize(text)))
            # Remove line wrapping only for detecting and retaining publication note.
            compact = re.sub(r'\s+', '', text)
            for m in NOTE_START.finditer(compact):
                if not wid:
                    continue
                combined = compact[m.start():]
                if i+1 < len(cleaned) and work_for.get(page+1) == wid:
                    combined += re.sub(r'\s+', '', cleaned[i+1])
                endmark = NOTE_END.search(combined)
                raw = combined[:endmark.start()] if endmark else combined[:650]
                db.execute('INSERT INTO notes(work_id,volume,pdf_page,printed_page,raw,status) VALUES(?,?,?,?,?,?)',
                           (wid,v,page,number,raw,'auto_candidate'))
                note_count += 1
            # Cross-page windows retain the exact source page boundary for lookup.
            if i+1 < len(cleaned):
                a,b = text[-650:],cleaned[i+1][:650]
                if not normalize(a) or not normalize(b):
                    continue
                # Only match strings crossing the boundary; server filters duplicates.
                db.execute('INSERT INTO pages(volume,pdf_page,printed_page,page_status,work_id,section,display_text,search_text,boundary) VALUES(?,?,?,?,?,?,?,?,?)',
                    (v,page,number,status,wid if wid == work_for.get(page+1) else None,section,
                     a+'\n'+b,normalize(a)+normalize(b),len(normalize(a))))
        report['volumes'].append({'volume':v,'pages':len(texts),'works':len(entries),
             'notes':note_count,'unknown_page_labels':sum(n is None for n,s in labels),
             'blank_pages':sum(not t.strip() for t in texts)})
        db.commit()
        print(f'  {len(texts)} 页 / {len(entries)} 个目录条目 / {note_count} 条发表信息候选', flush=True)
    report['built_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
    db.execute('INSERT INTO meta VALUES(?,?)', ('report',json.dumps(report,ensure_ascii=False)))
    db.commit()
    db.close()
    temp.replace(output)
    (output.parent/'import-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(f'索引已保存：{output}', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT/'data'/'luxun.sqlite')
    args = parser.parse_args()
    build(args.source.resolve(), args.output.resolve())
