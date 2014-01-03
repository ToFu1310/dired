
import sublime, sublime_plugin
from sublime import Region
from sublime_plugin import WindowCommand, EventListener, TextCommand
import os, re, shutil, tempfile
from os.path import basename, dirname, abspath, isdir, exists, join, isabs, normpath, normcase

# Each dired view will store its path in its local settings as 'dired_path'.

RE_FILE = re.compile(r'^([^\\// ].*)$')

map_wid_to_info = {}
# Map from window id that is displaying an input that needs completion to a CompletionInfo.

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
 p = move to previous file
 n = move to next file
 r = refresh view"""

RENAME_HELP = """\
 Rename files by editing them directly, then:
 Ctrl+Enter = apply changes
 Ctrl+Escape = discard changes"""


def reuse_view():
    return sublime.load_settings('dired.sublime-settings').get('reuse_view', False)


class DiredBaseCommand:
    """
    Convenience functions for dired TextCommands
    """
    @property
    def path(self):
        return self.view.settings().get('dired_path')

    def filecount(self):
        """
        Returns the number of files and directories in the view.
        """
        return self.view.settings().get('dired_count', 0)

    def move(self, forward=None):
        """
        Moves the cursor one line forward or backwards.  Clears all sections.
        """
        assert forward in (True, False), 'forward must be set to True or False'

        files = self.fileregion()
        if files.empty():
            return

        pt = self.view.sel()[0].a

        if files.contains(pt):
            # Try moving by one line.
            line = self.view.line(pt)
            pt = forward and (line.b + 1) or (line.a - 1)

        if not files.contains(pt):
            # Not (or no longer) in the list of files, so move to the closest edge.
            pt = (pt > files.b) and files.b or files.a

        line = self.view.line(pt)
        self.view.sel().clear()
        self.view.sel().add(Region(line.a, line.a))


    def fileregion(self):
        """
        Returns a region containing the lines containing filenames. If there are no filenames,
        this will be an empty region.
        """
        count = self.filecount()
        if count == 0:
            # Just the directory name.
            return Region(0, 0)
        return Region(self.view.text_point(2, 0), self.view.text_point(count+2, 0)-1)


    def get_all(self):
        """
        Returns a list of all filenames in the view.
        """
        return [ RE_FILE.match(self.view.substr(l)).group(1) for l in self.view.lines(self.fileregion()) ]


    def get_selected(self):
        """
        Returns a list of selected filenames.
        """
        names = set()
        fileregion = self.fileregion()
        for sel in self.view.sel():
            lines = self.view.lines(sel)
            for line in lines:
                if fileregion.contains(line):
                    text = self.view.substr(line)
                    names.add(RE_FILE.match(text).group(1))
        return sorted(list(names))

    def get_marked(self):
        lines = []
        for region in self.view.get_regions('marked'):
            lines.extend(self.view.lines(region))
        return [ RE_FILE.match(self.view.substr(line)).group(1) for line in lines ]

    def _mark(self, edit, mark=None, regions=None):
        """
        Marks the requested files.

        mark
            True, False, or a function with signature `func(oldmark, filename)`.  The function
            should return True or False.

        regions
            Either a single region or a sequence of regions.  Only files within the region will
            be modified.
        """
        # Allow the user to pass a single region or a collection (like view.sel()).
        if isinstance(regions, Region):
            regions = [ regions ]

        filergn = self.fileregion()

        # We can't update regions for a key, only replace, so we need to record the existing
        # marks.
        previous = self.view.get_regions('marked')
        marked = { RE_FILE.match(self.view.substr(r)).group(1): r for r in previous }

        for region in regions:
            for line in self.view.lines(region):
                if filergn.contains(line):
                    text = self.view.substr(line)
                    filename = RE_FILE.match(text).group(1)

                    if mark not in (True, False):
                        newmark = mark(filename in marked, filename)
                        assert newmark in (True, False), 'Invalid mark: {}'.format(newmark)
                    else:
                        newmark = mark

                    if newmark:
                        marked[filename] = line
                    else:
                        marked.pop(filename, None)

        if marked:
            r = sorted(list(marked.values()), key=lambda region: region.a)
            self.view.add_regions('marked', r, 'dired.marked', 'dot', 0)
        else:
            self.view.erase_regions('marked')


    def set_help_text(self, edit, text):
        # There is only 1 help text area, but the scope selector will skip blank lines
        # so use the union of all of the regions.
        regions = self.view.find_by_selector('comment.dired.help')
        region = regions[0]
        for other in regions[1:]:
            region = region.cover(other)
        start = region.begin()
        self.view.erase(edit, region)
        self.view.insert(edit, start, text)


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
            # A path was provided and we were requested to not ask for input.  Show the view.
            if not isdir(path):
                print('dired: Invalid path "{}"'.format(path))
            self._show_view(path)
        else:
            # Ask the user for the directory, starting from the one provided or the next best
            # choice.
            path = self._determine_path(path)

            if not path.endswith(os.sep):
                path += os.sep

            map_wid_to_info[self.window.id()] = CompletionInfo(self.window.id(), path)
            self.window.show_input_panel('Directory:', path, self.on_done, self.on_change, self.on_cancel)

    def _determine_path(self, path):
        """
        Determine the best path to start with.
        """
        # Use the provided path if it is valid.
        if path and isdir(path):
            return path

        # If the current file is associated with a file, open its directory.
        view = self.window.active_view()
        path = view.file_name()
        if path:
            return dirname(path)

        # If there is a project, use the folder.
        data = self.window.project_data()
        if data and 'folders' in data:
            folders = data['folders']
            if folders:
                return folders[0]['path']

        # Finally, default to the user's home directory.
        return os.path.expanduser('~')

    def _show_view(self, path):
        if not path.endswith(os.sep):
            path += os.sep

        for view in self.window.views():
            if view.settings().get('dired_path', None) == path:
                break
        else:
            view = self.window.new_file()
            view.set_scratch(True)
            view.set_name(basename(path.rstrip(os.sep)))
            view.settings().set('dired_path', path)

        view.settings().set('dired_rename_mode', False)
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
    def run(self, edit, new_view=False):
        path = self.path
        filenames = self.get_selected()

        # If reuse view is turned on, refresh the existing window if there is only one item and
        # it is a directory.
        if not new_view and reuse_view():
            if len(filenames) == 1 and isdir(join(path, filenames[0])):
                filename = filenames[0]
                self.view.set_name(filename.strip(os.sep))
                self.view.settings().set('dired_path', join(path, filename))
                self.view.settings().set('dired_rename_mode', False)
                self.view.run_command('dired_refresh')
                return

        for filename in filenames:
            fqn = join(path, filename)
            if isdir(fqn):
                self.view.window().run_command('dired', { 'path': fqn, 'input': False })
            else:
                self.view.window().open_file(fqn)


class DiredCreateCommand(TextCommand, DiredBaseCommand):
    def run(self, edit, which=None):
        assert which in ('file', 'directory'), "which: " + which

        # Is there a better way to do this?  Why isn't there some kind of context?  I assume
        # the command instance is global and really shouldn't have instance informaiton.
        callback = getattr(self, 'on_done_' + which, None)

        prompt = which.capitalize() + ':'
        self.view.window().show_input_panel(prompt, '', callback, None, None)

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
            if not exists(fqn):
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
            self._mark(edit, mark=_markfunc, regions=self.fileregion())

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

        self._mark(edit, mark=mark, regions=regions)

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

    def on_done(self, newname):
        newname = newname.strip()
        if not newname:
            return
        self.view.run_command('dired_rename', { 'newname': newname })


class DiredUpCommand(TextCommand, DiredBaseCommand):
    def run(self, edit):
        parent = dirname(self.path.rstrip(os.sep)) + os.sep
        if parent == self.path:
            return

        if reuse_view():
            self.view.set_name(basename(parent.rstrip(os.sep)))
            self.view.settings().set('dired_path', parent)
            self.view.run_command('dired_refresh')
        else:
            self.view.window().run_command('dired', { 'path': parent, 'input': False })
