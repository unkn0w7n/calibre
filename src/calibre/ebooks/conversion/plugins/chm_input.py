''' CHM File decoding support '''
__license__ = 'GPL v3'
__copyright__  = ('2008, Kovid Goyal <kovid at kovidgoyal.net>, '
                  'and Alex Bramley <a.bramley at gmail.com>.')

import os

from calibre.constants import filesystem_encoding
from calibre.customize.conversion import InputFormatPlugin
from calibre.ptempfile import TemporaryDirectory
from polyglot.builtins import as_bytes


class CHMInput(InputFormatPlugin):

    name        = 'CHM Input'
    author      = 'Kovid Goyal and Alex Bramley'
    description = _('Convert CHM files to OEB')
    file_types  = {'chm'}
    commit_name = 'chm_input'

    def _chmtohtml(self, output_dir, chm_path, no_images, log, debug_dump=False):
        from calibre.ebooks.chm.reader import CHMReader
        log.debug('Opening CHM file')
        rdr = CHMReader(chm_path, log, input_encoding=self.opts.input_encoding)
        log.debug(f'Extracting CHM to {output_dir}')
        rdr.extract_content(output_dir, debug_dump=debug_dump)
        self._chm_reader = rdr
        return rdr.hhc_path

    def convert(self, stream, options, file_ext, log, accelerators):
        from calibre.customize.ui import plugin_for_input_format
        from calibre.ebooks.chm.metadata import get_metadata_from_reader
        self.opts = options

        log.debug('Processing CHM...')
        with TemporaryDirectory('_chm2oeb') as tdir:
            if not isinstance(tdir, str):
                tdir = tdir.decode(filesystem_encoding)
            html_input = plugin_for_input_format('html')
            for opt in html_input.options:
                setattr(options, opt.option.name, opt.recommended_value)
            no_images = False  # options.no_images
            chm_name = stream.name
            # chm_data = stream.read()

            # closing stream so CHM can be opened by external library
            stream.close()
            log.debug(f'tdir={tdir}')
            log.debug(f'stream.name={stream.name}')
            debug_dump = False
            odi = options.debug_pipeline
            if odi:
                debug_dump = os.path.join(odi, 'input')
            mainname = self._chmtohtml(tdir, chm_name, no_images, log,
                    debug_dump=debug_dump)
            mainpath = os.path.join(tdir, mainname)

            try:
                metadata = get_metadata_from_reader(self._chm_reader)
            except Exception:
                log.exception('Failed to read metadata, using filename')
                from calibre.ebooks.metadata.book.base import Metadata
                metadata = Metadata(os.path.basename(chm_name))
            encoding = self._chm_reader.get_encoding() or options.input_encoding or 'cp1252'
            # print((tdir, mainpath))
            # from calibre import ipython
            # ipython()

            options.debug_pipeline = None
            options.input_encoding = 'utf-8'
            uenc = encoding
            if os.path.abspath(mainpath) in self._chm_reader.re_encoded_files:
                uenc = 'utf-8'
            htmlpath, toc = self._create_html_root(mainpath, log, uenc)
            self._chm_reader.CloseCHM()
            oeb = self._create_oebbook_html(htmlpath, tdir, options, log, metadata)
            options.debug_pipeline = odi
            if toc.count() > 1:
                oeb.toc = self.parse_html_toc(oeb.spine[0])
                oeb.manifest.remove(oeb.spine[0])
                oeb.auto_generated_toc = False
        return oeb

    def parse_html_toc(self, item):
        from calibre.ebooks.oeb.base import TOC, XPath
        dx = XPath('./h:div')
        ax = XPath('./h:a[1]')

        def do_node(parent, div):
            for child in dx(div):
                a = ax(child)[0]
                c = parent.add(a.text, a.attrib['href'])
                do_node(c, child)

        toc = TOC()
        root = XPath('//h:div[1]')(item.data)[0]
        do_node(toc, root)
        return toc

    def _create_oebbook_html(self, htmlpath, basedir, opts, log, mi):
        # use HTMLInput plugin to generate book
        from calibre.customize.builtins import HTMLInput
        opts.breadth_first = True
        opts.max_levels = 30
        opts.correct_case_mismatches = True
        htmlinput = HTMLInput(None)
        htmlinput.set_root_dir_of_input(basedir)
        htmlinput.root_dir_for_absolute_links = basedir
        oeb = htmlinput.create_oebbook(htmlpath, basedir, opts, log, mi)
        return oeb

    def _create_html_root(self, hhcpath, log, encoding):
        from lxml import html

        from calibre.ebooks.chardet import xml_to_unicode
        from calibre.ebooks.oeb.base import urlquote
        from polyglot.urllib import unquote as _unquote
        try:
            hhcdata = self._read_file(hhcpath)
        except FileNotFoundError:
            log.warn('No HHC file found in CHM, using the default topic as the first HTML file')
            from calibre.ebooks.oeb.base import TOC
            return os.path.join(os.path.dirname(hhcpath), self._chm_reader.relpath_to_first_html_file()), TOC()
        hhcdata = hhcdata.decode(encoding)
        hhcdata = xml_to_unicode(hhcdata, verbose=True,
                            strip_encoding_pats=True, resolve_entities=True)[0]
        hhcroot = html.fromstring(hhcdata)
        toc = self._process_nodes(hhcroot)
        # print('=============================')
        # print('Printing hhcroot')
        # print(etree.tostring(hhcroot, pretty_print=True))
        # print('=============================')
        log.debug(f'Found {toc.count()} section nodes')
        htmlpath = os.path.splitext(hhcpath)[0] + '.html'
        base = os.path.dirname(os.path.abspath(htmlpath))

        def unquote(x):
            if isinstance(x, str):
                x = x.encode('utf-8')
            return _unquote(x).decode('utf-8')

        def unquote_path(x):
            x, _, frag = x.partition('#')
            if frag:
                frag = '#' + frag
            if not os.path.exists(os.path.join(base, x)):
                y = unquote(x)
                if os.path.exists(os.path.join(base, y)):
                    x = y
            return x, frag

        def donode(item, parent, base, subpath):
            for child in item:
                title = child.title
                if not title:
                    continue
                raw, frag = unquote_path(child.href or '')
                rsrcname = os.path.basename(raw)
                rsrcpath = os.path.join(subpath, rsrcname)
                if (not os.path.exists(os.path.join(base, rsrcpath)) and os.path.exists(os.path.join(base, raw))):
                    rsrcpath = raw

                if '%' not in rsrcpath:
                    rsrcpath = urlquote(rsrcpath)
                if not raw:
                    rsrcpath = ''
                c = DIV(A(title, href=rsrcpath + frag))
                donode(child, c, base, subpath)
                parent.append(c)

        with open(htmlpath, 'wb') as f:
            if toc.count() > 1:
                from lxml.html.builder import BODY, DIV, HTML, A
                path0 = toc[0].href
                path0 = unquote_path(path0)[0]
                subpath = os.path.dirname(path0)
                base = os.path.dirname(f.name)
                root = DIV()
                donode(toc, root, base, subpath)
                raw = html.tostring(HTML(BODY(root)), encoding='utf-8',
                                   pretty_print=True)
                f.write(raw)
            else:
                f.write(as_bytes(hhcdata))
        return htmlpath, toc

    def _read_file(self, name):
        with open(name, 'rb') as f:
            data = f.read()
        return data

    def add_node(self, node, toc, ancestor_map):
        from calibre.ebooks.chm.reader import match_string
        if match_string(node.attrib.get('type', ''), 'text/sitemap'):
            p = node.xpath('ancestor::ul[1]/ancestor::li[1]/object[1]')
            parent = p[0] if p else None
            toc = ancestor_map.get(parent, toc)
            title = href = ''
            for param in node.xpath('./param'):
                if match_string(param.attrib['name'], 'name'):
                    title = param.attrib['value']
                elif match_string(param.attrib['name'], 'local'):
                    href = param.attrib['value']
            child = toc.add(title or _('Unknown'), href)
            ancestor_map[node] = child

    def _process_nodes(self, root):
        from calibre.ebooks.oeb.base import TOC
        toc = TOC()
        ancestor_map = {}
        for node in root.xpath('//object'):
            self.add_node(node, toc, ancestor_map)
        return toc
