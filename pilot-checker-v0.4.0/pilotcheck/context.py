"""Read stored HTML context without executing scripts or guessing selector matches."""
from html.parser import HTMLParser
import re

VOID = set('area base br col embed hr img input link meta param source track wbr'.split())


class Node:
    def __init__(self, tag, attrs, parent=None):
        self.tag, self.attrs, self.parent = tag, dict(attrs), parent
        self.children, self.parts = [], []

    def text(self):
        return ' '.join(' '.join(self.parts).split())[:300]


class Document(HTMLParser):
    def __init__(self, content):
        super().__init__(convert_charrefs=True)
        self.root = Node('root', [])
        self.current, self.nodes = self.root, []
        self.feed(content)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs, self.current)
        self.current.children.append(node)
        self.nodes.append(node)
        if tag not in VOID:
            self.current = node

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        p = self.current
        while p != self.root and p.tag != tag:
            p = p.parent
        if p != self.root:
            self.current = p.parent

    def handle_data(self, value):
        if self.current.tag in ('script', 'style'):
            return
        p = self.current
        # Keep only compact local context, never whole-page keyword matching.
        for _ in range(5):
            if p is None:
                break
            if sum(map(len, p.parts)) < 1200:
                p.parts.append(value)
            p = p.parent

    def select(self, selector):
        selectors = [p.strip() for p in selector.split('>')]

        def matches(node, part):
            # Support only the collector's tag/#id/:nth-of-type CSS subset.
            part = part.replace('\\/', '/')
            m = re.fullmatch(r'([\w-]+)(?:#([\w/.-]+))?(?::nth-of-type\((\d+)\))?', part)
            if not m or node is None:
                return False
            tag, ident, nth = m.groups()
            if node.tag != tag or (ident and node.attrs.get('id') != ident):
                return False
            if nth:
                siblings = [n for n in node.parent.children if n.tag == tag]
                if siblings.index(node) + 1 != int(nth):
                    return False
            return True

        hits = []
        for node in self.nodes:
            p = node
            for part in reversed(selectors):
                if not matches(p, part):
                    break
                p = p.parent
            else:
                hits.append(node)
        return hits

    def context(self, element):
        hits = self.select(element.get('css_path') or '')
        # Duplicate DOM IDs are common in saved banners. Require a single
        # candidate after comparing the recorded element's own identity.
        if len(hits) > 1:
            def agrees(n):
                for key, attr in [('id', 'id'), ('name', 'name'), ('href', 'href'),
                                  ('title', 'title'), ('aria_label', 'aria-label')]:
                    if element.get(key) and n.attrs.get(attr) != element[key]:
                        return False
                text = ' '.join(str(element.get('text') or '').split())
                return not text or n.text() == text
            hits = [n for n in hits if agrees(n)]
        if len(hits) != 1:
            if len(hits) > 1 and all(n.attrs == hits[0].attrs and n.text() == hits[0].text() for n in hits):
                return {'matched': False, 'matches': len(hits), 'consensus_attrs': hits[0].attrs,
                        'issue': 'identical contexts, activation target not unique'}
            return {'matched': False, 'matches': len(hits)}
        node = hits[0]
        # A selector hit must also agree with recorded identity.
        for key in ('id', 'name', 'href'):
            if element.get(key) and node.attrs.get(key) != element[key]:
                return {'matched': False, 'matches': 1, 'issue': 'identity mismatch'}
        ancestors = []
        p = node.parent
        while p and p.tag != 'root' and len(ancestors) < 5:
            ancestors.append({'tag': p.tag, 'id': p.attrs.get('id'),
                              'class': p.attrs.get('class'), 'role': p.attrs.get('role')})
            p = p.parent
        return {'matched': True, 'attrs': node.attrs, 'text': node.text(),
                'parent_text': node.parent.text(), 'ancestors': ancestors}
