"""One module per language, each a plain dict from the English sentence to its translation.

A dict rather than Qt's .ts/.qm files: those need lupdate and lrelease in the build, a
binary artefact inside the PyInstaller bundle, and a translator who has Qt Linguist. This
product ships as two exes and a script that runs pytest — a Python dict is greppable,
diffable in a pull request, and readable by anybody who can read the app.
"""
