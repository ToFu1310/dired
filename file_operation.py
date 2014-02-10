
import sublime
from sublime import Region
from sublime_plugin import WindowCommand, TextCommand
import os, shutil, tempfile
from os.path import basename, dirname, isdir, exists, join, isabs, normpath, normcase

from .common import RE_FILE, DiredBaseCommand
from . import prompt
from .show import show
from .dired import DiredCommand, DiredRefreshCommand, bookmarks, project, history
from queue import Queue
import threading
import sublime_plugin

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
 r = refresh view

 B = Goto Anywhere(goto any directory, bookmark or project dir) 
 ab = add to bookmark
 ap = add to project
 rb = remove from bookmark
 ra = remove from project

 P = toggle preview mode on/off

 j = jump to file/dir name """

RENAME_HELP = """\
 Rename files by editing them directly, then:
 Ctrl+Enter = apply changes
 Ctrl+Escape = discard changes"""

class DiredTestCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        cmd_list = ['test01', 'test02']

        def on_done(select):
            self.view.window().run_command('dired_file_operation', {'cmd':cmd_list[select]})

        self.view.window().show_quick_panel(cmd_list, on_done)



class DiredFileOperationCommand(sublime_plugin.WindowCommand):
    def run(self, cmd):
        DiredFileOperationThread(self.window, cmd).start()



class DiredFileOperationThread(threading.Thread):
    """
    A thread to prevent the listing of existing packages from freezing the UI
    """

    def __init__(self, window, cmd):
        """
        :param window:
            An instance of :class:`sublime.Window` that represents the Sublime
            Text window to show the list of installed packages in.
        """

        self.window = window
        threading.Thread.__init__(self)
        self.path_list, self.qp_list = self.make_path_list()
        self.cmd = cmd

    def run(self):
        sublime.set_timeout(self.window.show_quick_panel(self.qp_list, self.on_done), 10)

    def make_path_list(self):
        home = os.path.expanduser('~')
        bm = bookmarks()
        pr = project(self.window)
        hist = history()

        path_list = [home] + bm + pr + hist
        qp_list = []
        if home :
            qp_list.append('Home: ' + home)
        for item in bm :
            qp_list.append('Bookmark: ' + item)
        for item in pr :
            qp_list.append('Project: ' + item)
        for item in hist :
            qp_list.append('History: ' + item)
        qp_list.append('Goto directory')

        return path_list, qp_list


    def on_done(self, select):
        """
        Quick panel user selection handler - opens the homepage for any
        selected package in the user's browser

        :param picked:
            An integer of the 0-based package name index from the presented
            list. -1 means the user cancelled.
        """

        target = self.path_list[select]
        sublime.set_timeout(getattr(DiredFileOperationThread, self.cmd)(self, target), 10)

    def test01(self, target):
        print(self.cmd, target)

    def test02(self, target):
        print(self.cmd, target)


    # def _move(self, path):
    #     if path == self.path:
    #         return

    #     files = self.get_marked() or self.get_selected()

    #     if not isabs(path):
    #         path = join(self.path, path)
    #     if not isdir(path):
    #         sublime.error_message('Not a valid directory: {}'.format(path))
    #         return

    #     # Move all items into the target directory.  If the target directory was also selected,
    #     # ignore it.
    #     files = self.get_marked() or self.get_selected()
    #     path = normpath(normcase(path))
    #     for filename in files:
    #         fqn = normpath(normcase(join(self.path, filename)))
    #         if fqn != path:
    #             shutil.move(fqn, path)
    #     self.view.run_command('dired_refresh')


class DiredGotoTestCommand(TextCommand, DiredCommand):
    """
    This command is to go to a path selected with quick panel.

    The selectable paths are the path of current directory, home, bookmarks,
    project directories and inputted one.

    This was designed to be the alternative to dired and dired_goto command.

    This code is used the code of dired_select command.
    """
    def run(self, edit, new_view=False):
        self.window = self.view.window()
        path = self.view and self.view.file_name()
        home = os.path.expanduser('~')
        bm = bookmarks()
        pr = project(self.window)
        hist = history()

        qp_list = []
        if path and new_view :
            qp_list.append('Current dir: ' + os.path.split(path)[0])
        if home :
            qp_list.append('Home: ' + home)
        for item in bm :
            qp_list.append('Bookmark: ' + item)
        for item in pr :
            qp_list.append('Project: ' + item)
        for item in hist :
            qp_list.append('History: ' + item)
        qp_list.append('Goto directory')
        
        def on_done(select):
            if not select == -1 :
                fqn = qp_list[select]
                if 'Current dir' in fqn :
                    fqn = fqn[13:]
                elif 'Home' in fqn :
                    fqn = fqn[6:]
                elif 'Bookmark' in fqn :
                    fqn = fqn[10:]
                elif 'Project' in fqn :
                    fqn = fqn[9:]
                elif 'History' in fqn :
                    fqn = fqn[9:]
                elif 'Goto directory' in fqn :
                    prompt.start('Directory:', self.window, self._determine_path(), self._show)

                # If reuse view is turned on and the only item is a directory, 
                # refresh the existing view.
                if not new_view and reuse_view():
                    if isdir(fqn):
                        show(self.view.window(), fqn, view_id=self.view.id())
                        return

                if isdir(fqn):
                    show(self.view.window(), fqn, ignore_existing=new_view)

        self.view.window().show_quick_panel(qp_list, on_done)

