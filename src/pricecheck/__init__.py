"""Hotkey price checks on the item currently on the clipboard.

This is the package that makes the HTTP boundary in `src/service` earn its
keep. The stash pricer answers "what is my whole tab worth"; this answers "what
is *this* worth", in the half second between picking a map up and deciding
whether to list it.

The compliance posture is the reason this package is shaped the way it is, and
it is set out in full in `src/pricecheck/clipboard.py`. The short version: the
user presses Ctrl+C, which is Path of Exile's own built-in copy. Nothing here
sends a keystroke, a mouse event or a window message to the game, reads its
memory, or touches its files.
"""
