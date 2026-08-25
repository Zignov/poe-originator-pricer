"""The desktop overlay: the stash grid, drawn over the real stash tab.

Four modules, split along the line that matters for testing -- everything that
can be reasoned about without a screen is a plain function or dataclass, and the
tkinter layer is a thin renderer on top of it.

    state.py        the JSON contract between the Streamlit app and the overlay
    calibration.py  where the grid sits on screen, and how that is persisted
    layout.py       priced tab + calibration -> a list of rectangles to draw
    hotkeys.py      parsing "ctrl+alt+o", and the Windows hotkey listener
    window.py       the only module that imports tkinter

Nothing here touches the game. The overlay reads a JSON file this app wrote and
paints pixels on top of whatever is underneath; it never reads the client's
memory, never sends it input, and is click-through precisely so that it cannot.
"""
