
import sublime, sublime_plugin
from sublime import Region
from sublime_plugin import WindowCommand, EventListener, TextCommand
import os, re, shutil
from os.path import basename, dirname, abspath, isdir, exists, join, isabs, normpath, normcase

RE_DIR  = re.compile(r'^[ \*] (.*)(\\|/)$')
RE_FILE = re.compile(r'^[ \*] (.*)$') # also matches dir, so check RE_DIR first

FILENAME_OFFSET = 2

map_wid_to_info = {}
# Map from window id that is displaying an input that needs completion to a CompletionInfo.

class DiredBaseCommand:
    """
    Convenience functions for dired TextCommands
    """
    @property
    def path(self):
        return self.view.settings().get('dired')

    def linecount(self):
        """
        Returns the number of lines in the view.
        """
        return self.view.rowcol(self.view.size())[0] + 1

    def move(self, forward=None):
        """
        Moves the cursor one line forward or backwards.  Clears all sections.
        """
        assert forward in (True, False), 'forward must be set to True or False'

        files = self.itemregion()
        if files.empty():
            return

        pt = self.view.sel()[0].a

        print('start:', pt, files)

        if files.contains(pt):
            # Try moving by one line.
            line = self.view.line(pt)
            pt = forward and (line.b + 1) or (line.a - 1)
            print('moving:', line, pt)

        if not files.contains(pt):
            # Not (or no longer) in the list of files, so move to the closest edge.
            print('does not contain?', files, pt)
            pt = (pt > files.b) and files.b or files.a

        line = self.view.line(pt)
        pt = line.a + FILENAME_OFFSET

        self.view.sel().clear()
        self.view.sel().add(Region(pt, pt))

    def itemregion(self):
        """
        Returns a region containing the lines containing filenames. If there are no filenames,
        this will be an empty region.
        """
        count = self.linecount()
        if count == 1:
            # Just the directory name.
            return Region(0, 0)
        return Region(self.view.text_point(1, 0), self.view.size())


    def get_selected(self):
        """
        Returns a list of selected filenames.
        """
        names = set()
        itemregion = self.itemregion()
        for sel in self.view.sel():
            lines = self.view.lines(sel)
            for line in lines:
                if itemregion.contains(line):
                    text = self.view.substr(line)
                    names.add(RE_FILE.match(text).group(1))
        return sorted(list(names))

    def get_marked(self):
        names = []
        itemregion = self.itemregion()
        if not itemregion.empty():
            for line in self.view.lines(itemregion):
                text = self.view.substr(line)
                if text.startswith('*'):
                    names.append(RE_FILE.match(text).group(1))
        return names

    def _mark(self, edit, mark=None, regions=None):
        """
        Marks the requested files.

        mark
            Either ' ' or '*' to hardcode the mark to set, or a function `func(oldmark, filename)`
            that returns the mark (again ' ' or '*').

        regions
            Either a single region or a sequence of regions.  Only files within the region will
            be modified.
        """
        # Allow the user to pass a single region or a collection (like view.sel()).
        if isinstance(regions, Region):
            regions = [ regions ]

        self.view.set_read_only(False)

        filergn = self.itemregion()

        for region in regions:
            for line in self.view.lines(region):
                if filergn.contains(line):
                    text = self.view.substr(line)
                    oldmark  = text[0]
                    filename = RE_FILE.match(text).group(1)

                    if type(mark) is not str:
                        newmark = mark(oldmark, filename)
                        if newmark is None:
                            continue
                    else:
                        newmark = mark

                    self.view.replace(edit, Region(line.a, line.a + 1), newmark)

        self.view.set_read_only(True)


class CompletionInfo:
    def __init__(self, window_id, path):
        self.window_id = window_id
        self.view = None       # the completion view
        self.path = path

    def __repr__(self):
        return '{} {} view:{}'.format(self.window_id, self.path, bool(self.view))


class DiredCommand(WindowCommand):
    def run(self, path=None, input=True):
        """
        path
            The initial path to display.  When the command is triggered from the command
            palette or a keymap, this will be None and should default to the current file's
            current directory.  If not None, the command was already displaying an input panel
            and we just performed a completion.

        input
            Normally an input panel is displayed to allow the user to choose the directory to
            edit.  If a path is provided, this can be set to False to immediately display the
            directory view with no input panel.
        """
        if path and not input:
            if not isdir(path):
                print('dired: Invalid path "{}"'.format(path))
            self._show_view(path)
        else:
            if not path:
                view = self.window.active_view()
                path = view.file_name()
                if path:
                    path = dirname(path)

            path = path or ''

            if not path.endswith(os.sep) and isdir(path):
                path += os.sep

            map_wid_to_info[self.window.id()] = CompletionInfo(self.window.id(), path)
            self.window.show_input_panel('Directory:', path, self.on_done, self.on_change, self.on_cancel)


    def _show_view(self, path):
        view = self.window.new_file()
        view.set_scratch(True)
        view.set_name(basename(path.rstrip(os.sep)))
        view.settings().set('dired', path)
        self.window.focus_view(view)
        view.run_command('dired_refresh')

    def on_done(self, value):
        self._close_completions()
        self._show_view(value)

    def on_change(self, value):
        info = map_wid_to_info.get(self.window.id())
        if info:
            info.path = value

    def on_cancel(self):
        self._close_completions()

    def _close_completions(self):
        info = map_wid_to_info.pop(self.window.id(), None)
        if info and info.view:
            self.window.focus_view(info.view)
            self.window.run_command('close_file')


class DiredRefreshCommand(TextCommand, DiredBaseCommand):
    """
    Populates or repopulates a dired view.
    """
    def run(self, edit, goto=None):
        """
        goto
            Optional item name to put the cursor on.  If not provided, the cursor is put on the
            first item.
        """
        path = self.path

        names = os.listdir(path)
        f = []
        for name in names:
            if isdir(join(path, name)):
                name += os.sep
            f.append(name)

        marked = set(self.get_marked())

        text = [path]
        text.extend(['{} {}'.format(n in marked and '*' or ' ', n) for n in f])

        self.view.set_read_only(False)
        self.view.erase(edit, Region(0, self.view.size()))
        self.view.insert(edit, 0, '\n'.join(text))
        self.view.set_syntax_file('Packages/dired/dired.tmLanguage')
        self.view.set_read_only(True)

        # Place the cursor.
        if f:
            pt = self.itemregion().a
            if goto:
                if isdir(join(path, goto)) and not goto.endswith(os.sep):
                    goto += os.sep
                try:
                    line = f.index(goto) + 1
                    pt = self.view.text_point(line, 0)
                except ValueError:
                    pass

            self.view.sel().clear()
            self.view.sel().add(Region(pt + FILENAME_OFFSET, pt + FILENAME_OFFSET))


class DiredCompleteCommand(WindowCommand):

    def _needs_sep(self, path):
        """
        If the current value is a complete directory name without a trailing separator, and
        there are no other possible completions.
        """
        if not isdir(path) or path.endswith(os.sep):
            return False

        partial = basename(path)
        path    = dirname(path)
        if any(n for n in os.listdir(dirname(path)) if n != partial and n.startswith(partial) and isdir(join(path, n))):
            # There are other completions.
            return False

        return True

    def _parse_split(self, path):
        """
        Split the path into the directory to search and the prefix to match in that directory.

        If the path is completely invalid, (None, None) is returned.
        """
        prefix = ''

        if not path.endswith(os.sep):
            prefix = basename(path)
            path   = dirname(path)

        if not isdir(path):
            return (None, None)

        return (path, prefix)


    def _close_completions(self, info):
        if info.view:
            self.window.focus_view(info.view)
            self.window.run_command('close_file')
            info.view = None

    def run(self):
        info = map_wid_to_info.get(self.window.id())
        if not info:
            return

        path, prefix = self._parse_split(info.path)
        if path is None:
            print('Invalid:', info.path)
            return

        completions = [ n for n in os.listdir(path) if n.startswith(prefix) and isdir(join(path, n)) ]

        if len(completions) == 0:
            sublime.status_message('No matches')
            self._close_completions(info)
            return

        if len(completions) == 1:
            info.path = join(path, completions[0]) + os.sep
            self.window.run_command('dired', { 'path': info.path })
            self._close_completions(info)
            return

        common = os.path.commonprefix(completions)
        if common and common > prefix:
            info.path = join(path, common)
            self.window.run_command('dired', { 'path': info.path })
            self._close_completions(info)
            return

        # There are multiple possibilities.  Display a completion view.

        if not info.view:
            info.view = self.window.new_file()
            info.view.set_scratch(True)
            info.view.set_name('*completions*')

        info.view.run_command('dired_show_completions', { "completions": completions })
        self.window.focus_view(info.view)


class DiredShowCompletionsCommand(TextCommand):
    def run(self, edit, completions=None):
        self.view.erase(edit, Region(0, self.view.size()))
        self.view.insert(edit, 0, '\n'.join(completions))


class DiredEventListener(EventListener):
    def on_query_context(self, view, key, operator, operand, match_all):
        if not map_wid_to_info or not key.startswith('dired_'):
            return None
        if key == 'dired_complete':
            return True
        return False

# def getcompletions(path):

#     print('COMPLETIONS: path={!r} isdir='.format(path), isdir(path))
#     partial = ''

#     end = basename(path)

#     # Special case: If a path ends in '.', isdir returns true.
#     if end == '.' or not isdir(path):
#         partial = end
#         path    = dirname(path)
#         if not isdir(path):
#             return []

#     print('TEST:', path, partial)

#     c = [ name for name in os.listdir(path) if isdir(join(path, name)) ]
#     if partial:
#         c = [ name for name in c if name.startswith(partial) ]

#     return c

class DiredNextLineCommand(TextCommand, DiredBaseCommand):
    def run(self, edit, forward=None):
        self.move(forward)


class DiredSelect(TextCommand, DiredBaseCommand):
    def run(self, edit):
        path = self.path
        filenames = self.get_selected()
        for filename in filenames:
            fqn = join(path, filename)
            if isdir(fqn):
                self.view.window().run_command('dired', { 'path': fqn, 'input': False })
            else:
                self.view.window().open_file(fqn)


class DiredMkdir(TextCommand, DiredBaseCommand):
    def run(self, edit):
        self.view.window().show_input_panel('Directory:', '', self.on_done, None, None)

    def on_done(self, value):
        value = value.strip()
        if value:
            fqn = join(self.path, value)
            if not exists(fqn):
                os.makedirs(fqn)
            self.view.run_command('dired_refresh', {'goto': value})


class DiredMarkExtensionCommand(TextCommand, DiredBaseCommand):
    def run(self, edit, ext=None):
        filergn = self.itemregion()
        if filergn.empty():
            return

        if ext is None:
            # This is the first time we've been called, so ask for the extension.
            self.view.window().show_input_panel('Extension:', '', self.on_done, None, None)
        else:
            # We have already asked for the extension but had to re-run the command to get an
            # edit object.  (Sublime's command design really sucks.)
            def _markfunc(oldmark, filename):
                return filename.endswith(ext) and '*' or oldmark
            self._mark(edit, mark=_markfunc, regions=self.itemregion())

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
    def run(self, edit, mark='*', markall=False):
        assert mark in ('*', ' ', 't')

        filergn = self.itemregion()
        if filergn.empty():
            return

        # If markall is set, mark/unmark all files.  Otherwise only those that are selected.
        if markall:
            regions = [ filergn ]
        else:
            regions = self.view.sel()

        def _toggle(oldmark, filename):
            return (oldmark == ' ') and '*' or ' '
        if mark == 't':
            mark = _toggle

        self._mark(edit, mark=mark, regions=regions)

        # If there is no selection, move the cursor forward so the user can keep pressing 'm'
        # to mark successive files.
        if not markall and len(self.view.sel()) == 1 and self.view.sel()[0].empty():
            self.move(True)


class DiredDeleteCommand(TextCommand, DiredBaseCommand):
    def run(self, edit):
        files = self.get_marked()
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
        print('files:', files)
        if files:
            self.view.window().show_input_panel('Move to:', self.path, self.on_done, None, None)

    def on_done(self, value):
        if not isabs(value):
            value = join(self.path, value)
        if not isdir(value):
            sublime.error_message('Not a valid directory: {}'.format(value))
            return

        # Move all items into the target directory.  If the target directory was also selected,
        # ignore it.
        files = self.get_marked() or self.get_selected()
        value = normpath(normcase(value))
        for filename in files:
            fqn = normpath(normcase(join(self.path, filename)))
            if fqn != value:
                shutil.move(fqn, value)
        self.view.run_command('dired_refresh')

class DiredRenameCommand(TextCommand, DiredBaseCommand):
    def run(self, edit, newname=None):
        pt = self.view.sel()[0].a
        if not self.itemregion().contains(pt):
            return

        line = self.view.line(pt)
        text = self.view.substr(line)
        filename = RE_FILE.match(text).group(1)

        if not newname:
            self.view.window().show_input_panel('Rename:', filename, self.on_done, None, None)
        elif filename != newname:
            os.rename(join(self.path, filename), join(self.path, newname))
            self.view.run_command('dired_refresh', { 'goto': newname })

    def on_done(self, newname):
        newname = newname.strip()
        if not newname:
            return

        self.view.run_command('dired_rename', { 'newname': newname })
