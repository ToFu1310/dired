# SublimeText dired

A SublimeText 3 plugin that displays a directory in a view, allowing easy file manipulation,
loosely copied from emacs dired mode.

*WARNING:* The keybindings for the operations are experimental and may change in future
versions, particularly if more operations need to be added.

## Installation

You can install via [Sublime Package Control](http://wbond.net/sublime_packages/package_control)  
Or you can clone this repo into your *Sublime Text 3/Packages*

## Using

The plugin provides a `dired` command which allows you to choose a directory to display.  The
files in the directory are displayed in a list allowing them to be moved, renamed, or deleted.

There is no binding for the dired command (so run it from the Command Palette).

### Marking

Marked files have a '*' at the beginning of the line.

* `m` - Mark selected files.  If there is no selection, the file at the cursor
  is marked and the cursor is moved to the next line.

* `u` - Unmark selected files.  If there is no selection, the file at the cursor
  is unmarked and the cursor is moved to the next line.

* `U` - Unmark all files

* `t` - toggle all marks

* `*.` - (asterisk followed by period) Mark by file extension

To mark all files, use toggle.

### Operations

Note that these keybindings use capital letters.

* `D` - Delete marked files and directories (recursively) after confirmation.

  Since it is a dangerous command it only works with marked items, not selections or the
  cursor.

* `M` - Move marked or selected files to a target directory.

  If there are marked files, they will be moved.  Otherwise selected files or the file at the
  cursor.

* `R` - Rename the file the cursor is on.  This ignores marks.

### Other

* `r` - Refresh the view

## Themes

Unfortunately there are no obvious scopes to use, so you will probably need to update your
theme.  Suggestions for scopes are welcome.

* comment.dired.directory - The directory being viewed, at the top.  Defaults to the comment color.
* dired.item.directory
* dired.item.file
* dired.marked - marked files or directories.

Here is an example theme setting that displays directories in blue and marked items in red:

    <dict>
        <key>name</key>
        <string>dired path</string>
        <key>settings</key>
        <dict>
            <key>foreground</key>
            <string>#8080ff</string>
        </dict>
        <key>scope</key>
        <string>dired.item.directory</string>
    </dict>

    <dict>
        <key>name</key>
        <string>dired marked</string>
        <key>settings</key>
        <dict>
            <key>foreground</key>
            <string>#ff8080</string>
        </dict>
        <key>scope</key>
        <string>dired.marked</string>
    </dict>
