"""Where the pipeline's files live: deployment paths from the environment, the archviz spike's dirs by default."""

import os

SPIKE = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "spikes", "archviz"))
ASSETS = os.environ.get("PHOTO_ASSETS", os.path.join(SPIKE, "assets"))  # textures/, models/, hdri/
MADE = os.environ.get("PHOTO_MADE", os.path.join(SPIKE, "made"))  # made glbs, art/, stems/, objects.json
OBJECTS = os.environ.get("OBJ_DIR", os.path.join(ASSETS, "objects"))
CACHE = os.environ.get("PHOTO_CACHE", os.path.join(SPIKE, "work", "cache"))  # writable: cloth-simulated beds and baths
