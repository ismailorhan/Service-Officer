"""The panel's pages, one module per section of its menu.

They came out of ui/panel.py, which had grown to 2,400 lines and made
finding the page you wanted a search rather than a glance. Each page
still talks to the panel only through signals.
"""

from .categories import CategoriesPage
from .general import GeneralPage
from .history import HistoryPage
from .machines import MachinesPage
from .schedule import SchedulePage
from .services import ServiceDetail, ServicesPage
from .stacks import StackDetail, StacksPage

__all__ = ["CategoriesPage", "GeneralPage", "HistoryPage",
           "MachinesPage", "SchedulePage", "ServiceDetail",
           "ServicesPage", "StackDetail", "StacksPage"]
