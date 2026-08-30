'''
rig_tail_qt.py
author: Daisy Jane @gnitemouse

Qt binding for the tool windows, resolved once for the running Maya.

Maya moved from Qt5 to Qt6 in 2025, and the Python binding moved with it:

    Maya   Qt       binding              Python
    2024   5.15.2   PySide2/shiboken2    3.10.8
    2025   6.5.3    PySide6/shiboken6    3.11.4
    2026   6.5.3    PySide6/shiboken6    3.11.4
    2027   6.8.3    PySide6/shiboken6    3.13.9

No Maya ships both, so importing one and falling back to the other lands
on whichever is present. PySide6 is tried first: a studio that pip-installs
PySide2 alongside a Qt6 Maya would otherwise bind a Qt5 wrapper over a Qt6
runtime, which crashes rather than raising ImportError.

The windows only use QtWidgets, QtCore.Qt and wrapInstance, and every name
among them is spelled the same in both bindings (PySide6 keeps the short
enum form, Qt.AlignCenter, alongside the qualified one), so importing from
here needs no other change at the call site.

Names:
    QtWidgets, QtCore: the binding's modules
    wrapInstance: shiboken's pointer wrapper, for parenting to Maya
    QT_BINDING: 'PySide6' or 'PySide2', for logging and error messages
'''

__all__ = ['QtWidgets', 'QtCore', 'wrapInstance', 'QT_BINDING']

try:
    from PySide6 import QtWidgets, QtCore
    from shiboken6 import wrapInstance
    QT_BINDING = 'PySide6'
except ImportError:
    from PySide2 import QtWidgets, QtCore
    from shiboken2 import wrapInstance
    QT_BINDING = 'PySide2'
