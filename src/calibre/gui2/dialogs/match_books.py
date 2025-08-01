#!/usr/bin/env python


__license__   = 'GPL v3'
__copyright__ = '2013, Kovid Goyal kovid@kovidgoyal.net'
__docformat__ = 'restructuredtext en'


from qt.core import QAbstractItemView, QApplication, QCursor, QDialog, Qt, QTableWidgetItem, QTimer

from calibre.gui2 import error_dialog, gprefs
from calibre.gui2.dialogs.match_books_ui import Ui_MatchBooks
from calibre.utils.icu import sort_key
from calibre.utils.localization import ngettext
from calibre.utils.search_query_parser import ParseException


class TableItem(QTableWidgetItem):
    '''
    A QTableWidgetItem that sorts on a separate string and uses ICU rules
    '''

    def __init__(self, val, sort, idx=0):
        self.sort = sort
        self.sort_idx = idx
        QTableWidgetItem.__init__(self, val)
        self.setFlags(Qt.ItemFlag.ItemIsEnabled|Qt.ItemFlag.ItemIsSelectable)

    def __ge__(self, other):
        l = sort_key(self.sort)
        r = sort_key(other.sort)
        if l > r:
            return 1
        if l == r:
            return self.sort_idx >= other.sort_idx
        return 0

    def __lt__(self, other):
        l = sort_key(self.sort)
        r = sort_key(other.sort)
        if l < r:
            return 1
        if l == r:
            return self.sort_idx < other.sort_idx
        return 0


class MatchBooks(QDialog, Ui_MatchBooks):

    def __init__(self, gui, view, id_, row_index):
        QDialog.__init__(self, gui, flags=Qt.WindowType.Window)
        Ui_MatchBooks.__init__(self)
        self.setupUi(self)
        self.isClosed = False

        self.books_table_column_widths = None
        try:
            self.books_table_column_widths = \
                        gprefs.get('match_books_dialog_books_table_widths', None)
            self.restore_geometry(gprefs, 'match_books_dialog_geometry')
        except Exception:
            pass

        self.search_text.initialize('match_books_dialog')

        # Remove the help button from the window title bar
        icon = self.windowIcon()
        self.setWindowFlags(self.windowFlags()&(~Qt.WindowType.WindowContextHelpButtonHint))
        self.setWindowIcon(icon)

        self.device_db = view.model().db
        self.library_db = gui.library_view.model().db
        self.view = view
        self.gui = gui
        self.current_device_book_id = id_
        self.current_device_book_index = row_index
        self.current_library_book_id = None

        # Set up the books table columns
        self.books_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.books_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.books_table.setColumnCount(3)
        t = QTableWidgetItem(_('Title'))
        self.books_table.setHorizontalHeaderItem(0, t)
        t = QTableWidgetItem(_('Authors'))
        self.books_table.setHorizontalHeaderItem(1, t)
        t = QTableWidgetItem(ngettext('Series', 'Series', 1))
        self.books_table.setHorizontalHeaderItem(2, t)
        self.books_table_header_height = self.books_table.height()
        self.books_table.cellDoubleClicked.connect(self.book_doubleclicked)
        self.books_table.selectionModel().selectionChanged.connect(self.selection_changed)
        self.books_table.cellClicked.connect(self.book_clicked)
        self.books_table.sortByColumn(0, Qt.SortOrder.AscendingOrder)

        # get the standard table row height. Do this here because calling
        # resizeRowsToContents can word wrap long cell contents, creating
        # double-high rows
        self.books_table.setRowCount(1)
        self.books_table.setItem(0, 0, TableItem('A', ''))
        self.books_table.resizeRowsToContents()
        self.books_table_row_height = self.books_table.rowHeight(0)
        self.books_table.setRowCount(0)

        self.search_button.clicked.connect(self.do_search)
        self.search_button.setDefault(False)
        self.search_text.lineEdit().returnPressed.connect(self.return_pressed)

        self.buttonBox.accepted.connect(self.accept)
        self.buttonBox.rejected.connect(self.reject)
        self.ignore_next_key = False

        search_text = self.device_db[self.current_device_book_id].title
        search_text = search_text.replace('(', '\\(').replace(')', '\\)')
        self.search_text.setText(search_text)
        if search_text and len(self.library_db.new_api.all_book_ids()) < 8000:
            QTimer.singleShot(0, self.search_button.click)

    def return_pressed(self):
        self.ignore_next_key = True
        self.do_search()

    def keyPressEvent(self, e):
        if self.ignore_next_key:
            self.ignore_next_key = False
        else:
            QDialog.keyPressEvent(self, e)

    def do_search(self):
        query = str(self.search_text.text())
        if not query:
            d = error_dialog(self.gui, _('Match books'),
                     _('You must enter a search expression into the search field'))
            d.exec()
            return
        try:
            self.search_button.setEnabled(False)
            QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
            try:
                books = self.library_db.data.search(query, return_matches=True)
            except ParseException as e:
                return error_dialog(self.gui, _('Could not search'), _(
                    'The search expression {} is not valid.').format(query), det_msg=str(e), show=True)
            self.books_table.setRowCount(len(books))

            self.books_table.setSortingEnabled(False)
            for row, b in enumerate(books):
                mi = self.library_db.get_metadata(b, index_is_id=True, get_user_categories=False)
                a = TableItem(mi.title, mi.title_sort)
                a.setData(Qt.ItemDataRole.UserRole, b)
                self.books_table.setItem(row, 0, a)
                a = TableItem(' & '.join(mi.authors), mi.author_sort)
                self.books_table.setItem(row, 1, a)
                series = mi.format_field('series')[1]
                if series is None:
                    series = ''
                a = TableItem(series, mi.series, mi.series_index)
                self.books_table.setItem(row, 2, a)
                self.books_table.setRowHeight(row, self.books_table_row_height)

            self.books_table.setSortingEnabled(True)
        finally:
            self.search_button.setEnabled(True)
            QApplication.restoreOverrideCursor()

    # Deal with sizing the table columns. Done here because the numbers are not
    # correct until the first paint.
    def resizeEvent(self, *args):
        QDialog.resizeEvent(self, *args)
        if self.books_table_column_widths is not None:
            for c,w in enumerate(self.books_table_column_widths):
                self.books_table.setColumnWidth(c, w)
        else:
            # the vertical scroll bar might not be rendered, so might not yet
            # have a width. Assume 25. Not a problem because user-changed column
            # widths will be remembered
            w = self.books_table.width() - 25 - self.books_table.verticalHeader().width()
            w //= self.books_table.columnCount()
            for c in range(self.books_table.columnCount()):
                self.books_table.setColumnWidth(c, w)
        self.save_state()

    def selection_changed(self):
        x = self.books_table.selectedIndexes()
        if x:
            id_ = x[0].data(Qt.ItemDataRole.UserRole)
            self.current_library_book_id = id_

    def book_clicked(self, row, column):
        self.book_selected = True
        id_ = int(self.books_table.item(row, 0).data(Qt.ItemDataRole.UserRole))
        self.current_library_book_id = id_

    def book_doubleclicked(self, row, column):
        self.book_clicked(row, column)
        self.accept()

    def save_state(self):
        self.books_table_column_widths = []
        for c in range(self.books_table.columnCount()):
            self.books_table_column_widths.append(self.books_table.columnWidth(c))
        gprefs['match_books_dialog_books_table_widths'] = self.books_table_column_widths
        self.save_geometry(gprefs, 'match_books_dialog_geometry')
        self.search_text.save_history()

    def close(self):
        self.save_state()
        # clean up to prevent memory leaks
        self.device_db = self.view = self.gui = None

    def accept(self):
        if not self.current_library_book_id:
            d = error_dialog(self.gui, _('Match books'),
                     _('You must select a matching book'))
            d.exec()
            return
        mi = self.library_db.get_metadata(self.current_library_book_id,
                              index_is_id=True, get_user_categories=False,
                              get_cover=True)
        book = self.device_db[self.current_device_book_id]
        book.smart_update(mi, replace_metadata=True)
        self.gui.update_thumbnail(book)
        book.in_library_waiting = True
        self.view.model().current_changed(self.current_device_book_index,
                                          self.current_device_book_index)
        self.save_state()
        QDialog.accept(self)

    def reject(self):
        self.close()
        QDialog.reject(self)
