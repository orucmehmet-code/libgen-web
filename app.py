from flask import Flask, render_template, request, jsonify, Response
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlencode
import re
import os

app = Flask(__name__)

MIRRORS = [
    'https://libgen.vg',
    'https://libgen.li',
    'https://libgen.is',
    'https://libgen.rs',
    'https://libgen.st',
]

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

def find_mirror():
    for m in MIRRORS:
        try:
            r = SESSION.get(m, timeout=8)
            if r.status_code < 500:
                return m
        except:
            continue
    return None

def fmt_size(s):
    if not s: return ''
    s = str(s).strip().upper()
    if re.search(r'[KMGT]B', s): return s
    try:
        b = int(s)
        if b < 1024: return f'{b} B'
        elif b < 1024**2: return f'{b/1024:.0f} KB'
        else: return f'{b/1024**2:.1f} MB'
    except:
        return s

def search_books(mirror, query, field='def', ext='', lang='', page=1, res='25'):
    params = {'req': query, 'res': res, 'page': str(page)}
    field_map = {'title':'t','author':'a','publisher':'s','isbn':'i','def':''}
    col = field_map.get(field, '')
    if col: params['column'] = col

    col_list   = ['t','a','s','y','p','i']
    obj_list   = ['f','e','s','a','p','w']
    topic_list = ['l','c','f','s','m','r','a']

    qs = urlencode(params)
    for c in col_list:   qs += f'&columns%5B%5D={c}'
    for o in obj_list:   qs += f'&objects%5B%5D={o}'
    for t in topic_list: qs += f'&topics%5B%5D={t}'
    qs += '&filesuns=all'

    url = f'{mirror}/index.php?{qs}'
    r = SESSION.get(url, timeout=15)
    r.raise_for_status()

    soup = BeautifulSoup(r.text, 'html.parser')
    books = []

    table = None
    for t in soup.find_all('table'):
        headers = [th.get_text(strip=True).lower() for th in t.find_all('th')]
        if any(h in headers for h in ['ext.','mirrors','size','ext']):
            table = t
            break

    if not table:
        return books

    rows = table.find_all('tr')
    if not rows: return books
    headers = [th.get_text(strip=True).lower() for th in rows[0].find_all(['th','td'])]

    def col_idx(names):
        for name in names:
            for i, h in enumerate(headers):
                if name in h: return i
        return -1

    idx_title   = col_idx(['title'])
    idx_author  = col_idx(['author'])
    idx_year    = col_idx(['year'])
    idx_lang    = col_idx(['language'])
    idx_size    = col_idx(['size'])
    idx_ext     = col_idx(['ext'])
    idx_mirrors = col_idx(['mirrors'])

    for row in rows[1:]:
        cols = row.find_all('td')
        if len(cols) < 5: continue

        def get(idx):
            return cols[idx].get_text(strip=True) if 0 <= idx < len(cols) else ''

        title = ''
        md5 = ''

        if 0 <= idx_title < len(cols):
            td = cols[idx_title]
            a = td.find('a', href=True)
            if a:
                title = a.get_text(strip=True)
                href = a.get('href','')
                if 'md5=' in href:
                    md5 = href.split('md5=')[-1].split('&')[0].upper()

        mirror_links = []
        if 0 <= idx_mirrors < len(cols):
            for a in cols[idx_mirrors].find_all('a', href=True):
                href = a.get('href','')
                if href.startswith('http'):
                    mirror_links.append(href)
                elif href.startswith('/'):
                    mirror_links.append(f'{mirror}{href}')

        extension = get(idx_ext).lower()
        if ext and extension != ext: continue
        language = get(idx_lang)
        if lang and lang.lower() not in language.lower(): continue
        if not title: continue

        books.append({
            'title': title,
            'author': get(idx_author),
            'year': get(idx_year),
            'language': language,
            'size': fmt_size(get(idx_size)),
            'extension': extension,
            'md5': md5,
            'mirror_links': mirror_links,
        })

    return books

def get_download_url(book, mirror):
    md5 = book.get('md5','').lower()
    links = book.get('mirror_links', [])

    # Önce mirror link dene
    for link in links:
        try:
            r = SESSION.get(link, timeout=10)
            soup = BeautifulSoup(r.text, 'html.parser')
            # get.php linki ara
            for a in soup.find_all('a', href=True):
                href = a['href']
                if 'get.php' in href or 'download' in href.lower():
                    if href.startswith('http'):
                        return href
                    else:
                        from urllib.parse import urljoin
                        return urljoin(link, href)
        except:
            continue

    # MD5 ile dene
    if md5:
        for m in ['https://libgen.li', 'https://libgen.is']:
            try:
                url = f'{m}/get.php?md5={md5}'
                r = SESSION.head(url, timeout=8, allow_redirects=True)
                if r.status_code == 200:
                    return url
            except:
                continue

    return None

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/search')
def api_search():
    query = request.args.get('q','').strip()
    field = request.args.get('field','def')
    ext   = request.args.get('ext','')
    lang  = request.args.get('lang','')
    page  = int(request.args.get('page', 1))
    res   = request.args.get('res', '25')

    if not query:
        return jsonify({'error': 'Arama terimi gerekli'}), 400

    mirror = find_mirror()
    if not mirror:
        return jsonify({'error': 'Hiçbir mirror\'a ulaşılamadı'}), 503

    try:
        books = search_books(mirror, query, field, ext, lang, page, res)
        return jsonify({'books': books, 'mirror': mirror, 'page': page})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/download')
def api_download():
    md5   = request.args.get('md5','').lower()
    title = request.args.get('title', 'kitap')
    ext   = request.args.get('ext', 'pdf')
    mirror_links = request.args.getlist('links')

    book = {'md5': md5, 'mirror_links': mirror_links}
    mirror = find_mirror()

    url = get_download_url(book, mirror)
    if not url:
        return jsonify({'error': 'İndirme linki bulunamadı'}), 404

    try:
        r = SESSION.get(url, stream=True, timeout=30)
        r.raise_for_status()
        filename = f"{title[:50]}.{ext}".replace('/', '_').replace('\\', '_')
        return Response(
            r.iter_content(chunk_size=8192),
            headers={
                'Content-Disposition': f'attachment; filename="{filename}"',
                'Content-Type': r.headers.get('Content-Type', 'application/octet-stream'),
            }
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
v2
