
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


class DiredTestCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        cmd_list = ['move', 'copy']

        def on_done(select):
            if not select == -1 :
                self.view.window().run_command('dired_file_operation', {'cmd':cmd_list[select]})

        self.view.window().show_quick_panel(cmd_list, on_done)


class DiredFileOperationCommand(sublime_plugin.WindowCommand):
    def run(self, cmd):
        DiredFileOperationThread(self.window, cmd).start()



class DiredFileOperationThread(threading.Thread, DiredBaseCommand):
    """
    A thread to prevent the listing of existing packages from freezing the UI
    """

    def __init__(self, window, cmd):
        """
        """

        self.window = window
        self.view = self.window.active_view()
        threading.Thread.__init__(self)
        self.path_list, self.qp_list = self.make_path_list()
        self.cmd_name = cmd


    def run(self):
        sublime.set_timeout(self.window.show_quick_panel(self.qp_list, self.on_done), 10)


    def make_path_list(self):
        home = os.path.expanduser('~')
        bm = bookmarks()
        pr = project(self.window)
        hist = history()

        path_list = [home] + bm + pr + hist + ['Goto directory']
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
        """
        if not select == -1 :
            target = self.path_list[select]
            if target == 'Goto directory' :
                prompt.start(self.cmd_name + ' to:', self.window, self.path, self.operation)
            else :
                sublime.set_timeout(self.operation(target), 10)


    def make_path_available(self, path) :
        if path == self.path:
            return
        if not isabs(path):
            path = join(self.path, path)
        if not isdir(path):
            sublime.error_message('Not a valid directory: {}'.format(path))
            return
        return normpath(normcase(path))


    def operation(self, path):
        target = self.make_path_available(path)
        if not target :
            return
        items = self.get_marked() or self.get_selected()

        for itemname in items:
            fqn = normpath(normcase(join(self.path, itemname)))
            if fqn != target:
                getattr(shutil, self.cmd_name)(fqn, target)
        if self.cmd_name == 'move' :
            self.view.run_command('dired_refresh')
        self.view.run_command('dired_add_history', {'dirs':[target]})

