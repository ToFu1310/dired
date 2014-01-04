
import sublime
from sublime import Region
from sublime_plugin import WindowCommand, TextCommand
import os, shutil, tempfile
from os.path import basename, dirname, isdir, exists, join, isabs, normpath, normcase

from .common import RE_FILE, DiredBaseCommand
from . import prompt
from .show import show

# Each dired view stores its path in its local settings as 'dired_path'.

NORMAL_HELP = """\
 m = toggle mark
 t = toggle all marks
 U = unmark all
 *. = mark by file extension

 Enter/o = Open file / view directory
 R = rename
 M = move
 D = delete
 cd = create directory
 cf = create file

 u = up to parent directory
 g = goto directory
 p = move to previous file
 n = move to next file
 r = refresh view"""

RENAME_HELP = """\
 Rename files by editing them directly, then:
 Ctrl+Enter = apply changes
 Ctrl+Escape = discard changes"""


def reuse_view():
    return sublime.load_settings('dired.sublime-settings').get('reuse_view', False)


class DiredCommand(WindowCommand):
    """
    Prompt for a directory to display and display it.
    """
    def run(self):
        prompt.start('Directory:', self.window, self._determine_path(), self._show)

    def _show(self, path):
        show(self.window, path)

    def _determine_path(self):
        # Use the current view's directory if it has one.
        view = self.window.active_view()
        path = view and view.file_name()
        if path:
            return dirname(path)

        # Use the first project folder if there is one.
        data = self.window.project_data()
        if data and 'folders' in data:
            folders = data['folders']
            if folders:
                return folders[0]['path']

        # Use the user's home directory.
        return os.path.expanduser('~')


class DiredRefreshCommand(TextCommand, DiredBaseCommand):
    """
    Populates or repopulates a dired view.
    """
    def run(self, edit, goto=None):
        """
        goto
            Optional filename to put the cursor on.
        """
        path = self.path

        names = os.listdir(path)
        f = []
        for name in names:
            if isdir(join(path, name)):
                name += os.sep
            f.append(name)

        marked = set(self.get_marked())

        text = [ path ]
        text.append('')
        text.extend(f)
        text.append('')
        text.append(NORMAL_HELP)

        self.view.set_read_only(False)

        self.view.erase(edit, Region(0, self.view.size()))
        self.view.insert(edit, 0, '\n'.join(text))
        self.view.set_syntax_file('Packages/dired/dired.tmLanguage')
        self.view.settings().set('dired_count', len(f))

        if marked:
            # Even if we have the same filenames, they may have moved so we have to manually
            # find them again.
            regions = []
            for line in self.view.lines(self.fileregion()):
                filename = RE_FILE.match(self.view.substr(line)).group(1)
                if filename in marked:
                    regions.append(line)
            self.view.add_regions('marked', regions, 'dired.marked', 'dot', 0)
        else:
            self.view.erase_regions('marked')

        self.view.set_read_only(True)

        # Place the cursor.
        if f:
            pt = self.fileregion().a
            if goto:
                if isdir(join(path, goto)) and not goto.endswith(os.sep):
                    goto += os.sep
                try:
                    line = f.index(goto) + 2
                    pt = self.view.text_point(line, 0)
                except ValueError:
                    pass

            self.view.sel().clear()
            self.view.sel().add(Region(pt, pt))



class DiredNextLineCommand(TextCommand, DiredBaseCommand):
    def run(self, edit, forward=None):
        self.move(forward)


class DiredSelect(TextCommand, DiredBaseCommand):
    def run(self, edit, new_view=False):
        path = self.path
        filenames = self.get_selected()

        # If reuse view is turned on and the only item is a directory, refresh the existing view.
        if not new_view and reuse_view():
            if len(filenames) == 1 and isdir(join(path, filenames[0])):
                fqn = join(path, filenames[0])
                show(self.view.window(), fqn, view_id=self.view.id())
                return

        for filename in filenames:
            fqn = join(path, filename)
            if isdir(fqn):
                show(self.view.window(), fqn, ignore_existing=new_view)
            else:
                self.view.window().open_file(fqn)


class DiredCreateCommand(TextCommand, DiredBaseCommand):
    def run(self, edit, which=None):
        assert which in ('file', 'directory'), "which: " + which

        # Is there a better way to do this?  Why isn't there some kind of context?  I assume
        # the command instance is global and really shouldn't have instance information.
        callback = getattr(self, 'on_done_' + which, None)
        self.view.window().show_input_panel(which.capitalize() + ':', '', callback, None, None)

    def on_done_file(self, value):
        self._on_done('file', value)

    def on_done_directory(self, value):
        self._on_done('directory', value)

    def _on_done(self, which, value):
        value = value.strip()
        if not value:
            return

        fqn = join(self.path, value)
        if exists(fqn):
            sublime.error_message('{} already exists'.format(fqn))
            return

        if which == 'directory':
            os.makedirs(fqn)
        else:
            open(fqn, 'wb')

        self.view.run_command('dired_refresh', {'goto': value})


class DiredMarkExtensionCommand(TextCommand, DiredBaseCommand):
    def run(self, edit, ext=None):
        filergn = self.fileregion()
        if filergn.empty():
            return

        if ext is None:
            # This is the first time we've been called, so ask for the extension.
            self.view.window().show_input_panel('Extension:', '', self.on_done, None, None)
        else:
            # We have already asked for the extension but had to re-run the command to get an
            # edit object.  (Sublime's command design really sucks.)
            def _markfunc(oldmark, filename):
                return filename.endswith(ext) and True or oldmark
            self._mark(mark=_markfunc, regions=self.fileregion())

    def on_done(self, ext):
        ext = ext.strip()
        if not ext:
            return
        if not ext.startswith('.'):
            ext = '.' + ext
        self.view.run_command('dired_mark_extension', { 'ext': ext })

class DiredMarkCommand(TextCommand, DiredBaseCommand):
    """
    Marks or unmarks files.

    The mark can be set to '*' to mark a file, ' ' to unmark a file,  or 't' to toggle the
    mark.

    By default only selected files are marked, but if markall is True all files are
    marked/unmarked and the selection is ignored.

    If there is no selection and mark is '*', the cursor is moved to the next line so
    successive files can be marked by repeating the mark key binding (e.g. 'm').
    """
    def run(self, edit, mark=True, markall=False):
        assert mark in (True, False, 'toggle')

        filergn = self.fileregion()
        if filergn.empty():
            return

        # If markall is set, mark/unmark all files.  Otherwise only those that are selected.
        if markall:
            regions = [ filergn ]
        else:
            regions = self.view.sel()

        def _toggle(oldmark, filename):
            return not oldmark
        if mark == 'toggle':
            # Special internal case.
            mark = _toggle

        self._mark(mark=mark, regions=regions)

        # If there is no selection, move the cursor forward so the user can keep pressing 'm'
        # to mark successive files.
        if not markall and len(self.view.sel()) == 1 and self.view.sel()[0].empty():
            self.move(True)


class DiredDeleteCommand(TextCommand, DiredBaseCommand):
    def run(self, edit):
        files = self.get_marked() or self.get_selected()
        if files:
            # Yes, I know this is English.  Not sure how Sublime is translating.
            if len(files) == 1:
                msg = "Delete {}?".format(files[0])
            else:
                msg = "Delete {} items?".format(len(files))
            if sublime.ok_cancel_dialog(msg):
                for filename in files:
                    fqn = join(self.path, filename)
                    if isdir(fqn):
                        shutil.rmtree(fqn)
                    else:
                        os.remove(fqn)
                self.view.run_command('dired_refresh')


class DiredMoveCommand(TextCommand, DiredBaseCommand):
    def run(self, edit):
        files = self.get_marked() or self.get_selected()
        if files:
            prompt.start('Move to:', self.view.window(), self.path, self._move)

    def _move(self, path):
        if path == self.path:
            return

        files = self.get_marked() or self.get_selected()

        if not isabs(path):
            path = join(self.path, path)
        if not isdir(path):
            sublime.error_message('Not a valid directory: {}'.format(path))
            return

        # Move all items into the target directory.  If the target directory was also selected,
        # ignore it.
        files = self.get_marked() or self.get_selected()
        path = normpath(normcase(path))
        for filename in files:
            fqn = normpath(normcase(join(self.path, filename)))
            if fqn != path:
                shutil.move(fqn, path)
        self.view.run_command('dired_refresh')


class DiredRenameCommand(TextCommand, DiredBaseCommand):
    def run(self, edit):
        if self.filecount():
            # Store the original filenames so we can compare later.
            self.view.settings().set('rename', self.get_all())
            self.view.settings().set('dired_rename_mode', True)
            self.view.set_read_only(False)
            self.set_help_text(edit, RENAME_HELP)


class DiredRenameCancelCommand(TextCommand, DiredBaseCommand):
    """
    Cancel rename mode.
    """
    def run(self, edit):
        self.view.settings().erase('rename')
        self.view.settings().set('dired_rename_mode', False)
        self.view.run_command('dired_refresh')


class RenameError(Exception):
    pass


class DiredRenameCommitCommand(TextCommand, DiredBaseCommand):
    def run(self, edit):
        if not self.view.settings().has('rename'):
            # Shouldn't happen, but we want to cleanup when things go wrong.
            self.view.run_command('dired_refresh')
            return

        # The user *might* have messed up the buffer badly so we can't just use the same file
        # region without some verification.  We'll check every line in the buffer and make sure
        # that no lines look like filenames that are outside of the old filename area and that
        # there are the same number of filenames.  You can rename a file to just about
        # anything, so I'm not going to validate that right now.

        before = self.view.settings().get('rename')

        try:
            start = 2
            stop  = start + len(before)
            after = []

            for lineno, line in enumerate(self.view.lines(Region(0, self.view.size()))):
                text  = self.view.substr(line)
                match = RE_FILE.match(text)
                if match:
                    if not start <= lineno < stop:
                        print('INVALID LINE:', lineno, text)
                        raise RenameError('Line {} should not be modified'.format(lineno+1))
                    after.append(match.group(1))

            if len(after) != len(before):
                raise RenameError('You cannot add or remove lines')

            if len(set(after)) != len(after):
                raise RenameError('There are duplicate filenames')

            diffs = [ (b, a) for (b, a) in zip(before, after) if b != a ]
            if diffs:
                existing = set(before)
                while diffs:
                    b, a = diffs.pop(0)

                    if a in existing:
                        # There is already a file with this name.  Give it a temporary name (in
                        # case of cycles like "x->z and z->x") and put it back on the list.
                        tmp = tempfile.NamedTemporaryFile(delete=False, dir=self.path).name
                        os.unlink(tmp)
                        diffs.append((tmp, a))
                        a = tmp

                    print('dired rename: {} --> {}'.format(b, a))
                    os.rename(join(self.path, b), join(self.path, a))
                    existing.remove(b)
                    existing.add(a)

            self.view.settings().erase('rename')
            self.view.settings().set('dired_rename_mode', False)
            self.view.run_command('dired_refresh')

        except RenameError as ex:
            sublime.error_message(ex)


class DiredUpCommand(TextCommand, DiredBaseCommand):
    def run(self, edit):
        parent = dirname(self.path.rstrip(os.sep)) + os.sep
        if parent == self.path:
            return

        view_id = (self.view.id() if reuse_view() else None)
        show(self.view.window(), parent, view_id, goto=basename(self.path.rstrip(os.sep)))


class DiredGotoCommand(TextCommand, DiredBaseCommand):
    """
    Prompt for a new directory.
    """
    def run(self, edit):
        prompt.start('Goto:', self.view.window(), self.path, self.goto)

    def goto(self, path):
        show(self.view.window(), path, view_id=self.view.id())
