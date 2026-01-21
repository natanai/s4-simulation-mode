from .version import BUILD_NUMBER, __version__

# Import commands module for side-effects (console command registration).
from . import commands  # noqa: F401
