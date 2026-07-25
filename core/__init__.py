"""Service Officer core — everything that isn't the user interface.

Nothing in this package may import a UI toolkit. That keeps the interesting
behaviour (SCM notifications, recovery rules, ordered stack runs, history)
testable without a display, and made the move off tkinter a UI-only change.
"""
