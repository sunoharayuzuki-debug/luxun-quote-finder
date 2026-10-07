"""Portable JSON CLI for the skill. Querying needs only the standard library."""
import argparse
from contextlib import redirect_stdout
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys

from search_core import search

SKILL = Path(__file__).resolve().parents[1]


def database_path(explicit=None):
    value = explicit or os.environ.get('LUXUN_DATABASE')
    if not value and (SKILL/'local.json').exists():
        value = json.loads((SKILL/'local.json').read_text(encoding='utf-8')).get('database')
    return Path(value).expanduser().resolve() if value else Path.home()/'.local/share/luxun-text-search/luxun.sqlite'


def read_db(path):
    if not path.is_file():
        raise ValueError('没有本地索引。请提供 --database，或先用 import 导入自己的 OCR PDF。')
    db = sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
    db.row_factory = sqlite3.Row
    return db


def main(argv=None):
    parser=argparse.ArgumentParser(description='鲁迅本地引文检索：JSON 输出，不含书籍数据。')
    subs=parser.add_subparsers(dest='command',required=True)
    for name in ['doctor','import','search','page']:
        sub=subs.add_parser(name)
        sub.add_argument('--database',help='索引路径；未指定时读取环境或本地配置')
        if name=='import':
            sub.add_argument('--source',required=True,type=Path)
            sub.add_argument('--rebuild',action='store_true')
        if name=='search':
            sub.add_argument('--query',required=True)
            sub.add_argument('--volume',type=int,choices=range(1,19))
            sub.add_argument('--limit',type=int,default=5,choices=range(1,21))
            sub.add_argument('--approximate',action='store_true')
        if name=='page':
            sub.add_argument('--volume',type=int,required=True,choices=range(1,19))
            sub.add_argument('--page',type=int,required=True)
    args=parser.parse_args(argv)
    try:
        path=database_path(args.database)
        if args.command=='doctor':
            result={'python':sys.version.split()[0], 'python_supported':sys.version_info >= (3,10),
                    'pdf_import_available':bool(importlib.util.find_spec('pypdf')),
                    'database':str(path), 'database_exists':path.is_file(),
                    'network_calls':False}
            if path.is_file():
                db=read_db(path)
                try:
                    result['volumes']=db.execute('SELECT count(*) FROM sources').fetchone()[0]
                finally: db.close()
        elif args.command=='import':
            source=args.source.expanduser().resolve()
            if not source.is_dir(): raise ValueError('语料目录不存在。')
            if path.exists() and not args.rebuild:
                raise ValueError('索引已存在，未改动。确认需要重建时使用 --rebuild。')
            if not importlib.util.find_spec('pypdf'):
                raise ValueError('导入需要 pypdf，请在所用 Python 环境安装 requirements.txt。')
            from build_index import build
            with redirect_stdout(sys.stderr): build(source,path)
            result={'database':str(path),'report':str(path.parent/'import-report.json'),'imported':True}
        else:
            db=read_db(path)
            try:
                if args.command=='search':
                    result=search(db,args.query,args.volume,args.approximate,args.limit)
                    result['database']=str(path)
                    for item in result['results']:
                        row=db.execute('SELECT path FROM sources WHERE volume=?',(item['volume'],)).fetchone()
                        item['source_pdf']=row['path'] if row else None
                        item['source_pdf_exists']=bool(row and Path(row['path']).is_file())
                        item['notes_total']=len(item['notes'])
                        item['notes']=item['notes'][:3]
                        for note in item['notes']:
                            note['raw_truncated']=len(note['raw'])>280
                            note['raw']=note['raw'][:280]
                    result['verification']='OCR及自动关联候选，需按扫描页核验；初刊信息不证明句式与初刊一致。'
                else:
                    row=db.execute('SELECT p.volume,p.pdf_page,p.printed_page,p.page_status,p.display_text,s.path AS source_pdf FROM pages p JOIN sources s ON p.volume=s.volume WHERE p.volume=? AND p.pdf_page=? AND p.boundary=0',
                                   (args.volume,args.page)).fetchone()
                    if not row: raise ValueError('未找到指定 PDF 页。')
                    result=dict(row)
                    result['truncated']=len(result['display_text'])>6000
                    result['display_text']=result['display_text'][:6000]
            finally: db.close()
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0
    except (ValueError,OSError,sqlite3.Error,ImportError) as exc:
        print(json.dumps({'error':str(exc)},ensure_ascii=False))
        return 2


if __name__=='__main__':
    sys.exit(main())
