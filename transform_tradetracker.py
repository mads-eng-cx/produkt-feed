#!/usr/bin/env python3
"""
Renser Cykelexpertens TradeTracker-feed (RSS/Google Shopping, <item>-elementer)
til Shopify 'products.json'-format - et produkt pr. g:item_group_id, varianter
samlet indeni. Til brug som AI-chat-feed.

Hver variant beholder de paene Shopify-felter PLUS et 'source'-objekt med de
oprindelige felter. Felter i EXCLUDE_FIELDS udelades helt fra output.
Felter der optraeder flere gange i et item bevares som lister.

Gruppering:
  - <item> med samme <g:item_group_id> -> et produkt med flere varianter.
  - <item> UDEN item_group_id -> Simple-produkt med en variant.

Priser i kroner. Streaming (iterparse + clear) -> lavt hukommelsesforbrug.
"""

import sys
import re
import html
import json
import hashlib
import datetime
import xml.etree.ElementTree as ET

_TAG_RE = re.compile(r'<[^>]+>')
_WS_RE = re.compile(r'\s+')
_NUM_RE = re.compile(r'[\d]+(?:[.,]\d+)?')

# Felter der IKKE maa komme med i output-feedet (hverken Shopify-felt eller source)
EXCLUDE_FIELDS = {'google_product_category', 'tags', 'bikedeskStock'}


def local(tag):
    if tag is None:
        return ''
    if '}' in tag:
        tag = tag.split('}', 1)[1]
    if ':' in tag:
        tag = tag.split(':', 1)[1]
    return tag


def first(v):
    if isinstance(v, list):
        return v[0] if v else ''
    return v or ''


def stable_id(*parts):
    key = '|'.join(p for p in parts if p)
    return int(hashlib.sha1(key.encode('utf-8')).hexdigest()[:15], 16)


def html_to_text(s):
    if not s:
        return ''
    s = html.unescape(s)
    s = _TAG_RE.sub(' ', s)
    s = html.unescape(s)
    return _WS_RE.sub(' ', s).strip()


def slugify(s):
    s = s.lower().replace('æ', 'ae').replace('ø', 'oe').replace('å', 'aa')
    return re.sub(r'[^a-z0-9]+', '-', s).strip('-')


def money(s):
    if not s:
        return None
    m = _NUM_RE.search(s)
    if not m:
        return None
    try:
        return '{:.2f}'.format(float(m.group(0).replace(',', '.')))
    except ValueError:
        return None


def parse_item(elem):
    d = {}
    for child in elem:
        name = local(child.tag)
        if name == 'shipping':
            sp = ''
            for sub in child:
                if local(sub.tag) == 'price':
                    sp = (sub.text or '').strip()
            d['shipping_price'] = sp
            continue
        val = (child.text or '').strip()
        if name in d:
            if not isinstance(d[name], list):
                d[name] = [d[name]]
            d[name].append(val)
        else:
            d[name] = val
    return d


def product_title(d):
    t = first(d.get('title', ''))
    return t.rsplit(' - ', 1)[0].strip() if ' - ' in t else t


def color_of(d):
    return first(d.get('Farve')) or first(d.get('Farvevælger')) or ''


def size_of(d):
    return (first(d.get('variant')) or first(d.get('Tøjstørrelser'))
            or first(d.get('Skostørrelser')) or '')


def variant_price(d):
    listep = money(first(d.get('price')))
    sale = money(first(d.get('sale_price')))
    if sale and listep and float(sale) < float(listep):
        return sale, listep
    return (listep or sale), None


def build_product(group_id, items, feed_ts):
    rep = items[0]
    pid = stable_id(group_id or first(rep.get('id')) or first(rep.get('ShopwareID')))
    title = product_title(rep)

    has_color = any(color_of(v) for v in items)
    has_size = any(size_of(v) for v in items)
    dims = []
    if has_color:
        dims.append(('Farve', color_of))
    if has_size:
        dims.append(('Størrelse', size_of))
    if not dims:
        dims = [('Title', lambda v: 'Default')]

    options = []
    for i, (name, fn) in enumerate(dims, start=1):
        vals = list(dict.fromkeys(fn(v) for v in items if fn(v)))
        options.append({'name': name, 'position': i, 'values': vals})

    img_objs, seen = [], {}
    for v in items:
        src = first(v.get('image_link', ''))
        if src and src not in seen:
            seen[src] = stable_id(src)
            img_objs.append({'id': seen[src], 'position': len(img_objs) + 1,
                             'product_id': pid, 'variant_ids': [], 'src': src})

    out_variants = []
    for pos, v in enumerate(items, start=1):
        vid = stable_id(first(v.get('id')) or first(v.get('ShopwareID')) or (group_id + str(pos)))
        vals = [fn(v) for (_n, fn) in dims]
        vals += [None] * (3 - len(vals))
        vtitle = ' / '.join(x for x in vals[:len(dims)] if x) or 'Default Title'
        price, compare = variant_price(v)
        src = first(v.get('image_link', ''))
        if src and src in seen:
            for io_ in img_objs:
                if io_['src'] == src:
                    io_['variant_ids'].append(vid)
        out_variants.append({
            'id': vid,
            'title': vtitle,
            'option1': vals[0],
            'option2': vals[1],
            'option3': vals[2],
            'sku': first(v.get('mpn')) or first(v.get('id')) or None,
            'barcode': first(v.get('gtin')) or None,
            'requires_shipping': True,
            'taxable': True,
            'featured_image': ({'src': src} if src else None),
            'available': (first(v.get('availability')).lower() == 'in_stock'),
            'price': price,
            'compare_at_price': compare,
            'grams': 0,
            'position': pos,
            'product_id': pid,
            'created_at': feed_ts,
            'updated_at': feed_ts,
            'source': {k: val for k, val in v.items() if k not in EXCLUDE_FIELDS},
        })

    return {
        'id': pid,
        'title': title,
        'handle': slugify(title),
        'body_html': html_to_text(first(rep.get('description', ''))),
        'published_at': feed_ts,
        'created_at': feed_ts,
        'updated_at': feed_ts,
        'vendor': first(rep.get('brand', '')),
        'product_type': first(rep.get('product_type', '')),
        'variants': out_variants,
        'images': img_objs,
        'options': options,
    }


def transform(infile, outfile):
    groups, order, simples = {}, [], []
    feed_ts = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')
    ctx = ET.iterparse(infile, events=('end',))
    n = 0
    for ev, elem in ctx:
        if local(elem.tag) != 'item':
            continue
        d = parse_item(elem)
        gid = d.get('item_group_id', '')
        if isinstance(gid, list):
            gid = gid[0] if gid else ''
        if gid:
            if gid not in groups:
                groups[gid] = []
                order.append(gid)
            groups[gid].append(d)
        else:
            simples.append(d)
        n += 1
        elem.clear()

    products = [build_product(g, groups[g], feed_ts) for g in order]
    products += [build_product('', [d], feed_ts) for d in simples]

    with open(outfile, 'w', encoding='utf-8') as out:
        json.dump({'products': products}, out, ensure_ascii=False)

    print('Laeste varianter (items):', n)
    print('Produkter ud           :', len(products),
          '(grupperede:', len(order), '| simple:', len(simples), ')')


if __name__ == '__main__':
    inp = sys.argv[1] if len(sys.argv) > 1 else 'tradetracker.xml'
    outp = sys.argv[2] if len(sys.argv) > 2 else 'products.json'
    transform(inp, outp)
