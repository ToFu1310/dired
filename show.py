
import os
from os.path import basename
from .common import first

def show(window, path, view_id=None, ignore_existing=False, goto=None):
    """
    Determines the correct view to use, creating one if necessary, and prepares it.
    """
    if not path.endswith(os.sep):
        path += os.sep

    view = None
    if view_id:
        # The Goto command was used so the view is already known and its contents should be
        # replaced with the new path.
        view = first(window.views(), lambda v: v.id() == view_id)

    if not view and not ignore_existing:
        # See if a view for this path already exists.
        view = first(window.views(), lambda v: v.settings().get('dired_path') == path)

    if not view:
        view = window.new_file()
        view.set_scratch(True)

    view.set_name(basename(path.rstrip(os.sep)))
    view.settings().set('dired_path', path)
    view.settings().set('dired_rename_mode', False)
    window.focus_view(view)
    view.run_command('dired_refresh', { 'goto': goto })
